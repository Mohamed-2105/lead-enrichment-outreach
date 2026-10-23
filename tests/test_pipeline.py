import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from leadrouter import pipeline as p
from leadrouter import settings
from tests.helpers import Base, make_enrichment

SECRET = "fake-key-for-tests-0123456789"


class RowsTest(unittest.TestCase):
    def test_every_row_has_every_column_in_the_same_order(self):
        scrape = dict(p.NO_SCRAPE)
        a = p.unscored_row({"company_name": "A", "country": "FR"}, scrape, "To check - x")
        b = p.build_row({"company_name": "B", "country": ""}, routing_status="Tier 1", score=8)
        self.assertEqual(list(a.keys()), list(b.keys()))
        self.assertEqual(list(a.keys())[2:], ["routing_status", "score", "score_breakdown", "error"] + p.OUTPUT_COLUMNS[4:])

    def test_unknown_output_column_is_refused(self):
        with self.assertRaises(KeyError):
            p.build_row({"company_name": "A"}, not_a_column=1)

    def test_secrets_never_reach_the_error_column(self):
        with mock.patch.object(settings, "APOLLO_API_KEY", SECRET):
            row = p.unscored_row({"company_name": "A"}, dict(p.NO_SCRAPE), "Error", error=f"failed with key {SECRET}")
        self.assertNotIn(SECRET, row["error"])
        self.assertIn("[hidden]", row["error"])

    def test_country_decision(self):
        self.assertEqual(p.decide_country("FR", "https://x.de", "ES"), ("FR", "csv"))
        self.assertEqual(p.decide_country("", "https://x.de", "ES"), ("DE", "domain"))
        self.assertEqual(p.decide_country("", "https://x.com", "ES"), ("ES", "model"))
        self.assertEqual(p.decide_country("", "https://x.com", ""), ("", ""))


class PrepareTest(unittest.TestCase):
    def test_same_company_written_twice_is_merged_and_the_fuller_row_wins(self):
        df = pd.DataFrame([
            {"company_name": "Alba et Bruno", "website_url": "", "country": ""},
            {"company_name": "Alba & Bruno", "website_url": "https://albaetbruno.com", "country": "FR"},
            {"company_name": "Orchid", "website_url": "", "country": ""},
            {"company_name": "Orchid Cosmetics", "website_url": "", "country": ""},
            {"company_name": "PLUMO", "website_url": "", "country": ""},
            {"company_name": "Plumo", "website_url": "", "country": ""},
        ])
        out = p.dedupe_by_name(df)
        self.assertEqual(list(out["company_name"]), ["Alba & Bruno", "Orchid", "Orchid Cosmetics", "PLUMO"])

    def test_a_csv_with_only_company_names_works(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "leads.csv"
            path.write_text("company_name\nAlpha\nBeta\n", encoding="utf-8")
            with mock.patch.object(p, "find_website", side_effect=lambda name, country: (f"https://{name.lower()}.com", "high", "ok")):
                df = p.prepare_leads(str(path))
        self.assertEqual(list(df["clean_domain"]), ["alpha.com", "beta.com"])
        self.assertEqual(set(df["website_source"]), {"search"})

    def test_a_csv_without_company_name_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "leads.csv"
            path.write_text("name\nAlpha\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                p.prepare_leads(str(path))

    def test_test_mode_cuts_the_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "leads.csv"
            path.write_text("company_name,website_url\nA,https://a.com\nB,https://b.com\nC,https://c.com\n", encoding="utf-8")
            with mock.patch.object(settings, "MAX_LEADS", 2):
                self.assertEqual(len(p.prepare_leads(str(path))), 2)


class OutputTest(unittest.TestCase):
    def test_rows_are_appended_after_a_header_and_multiline_cells_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "sub" / "out.csv")
            p.append_row(path, {"company_name": "A", "cold_email_draft": "Hi,\n\nBest", "employees": 120}, first=True)
            p.append_row(path, {"company_name": "B", "cold_email_draft": None, "employees": float("nan")}, first=False)
            with open(path, newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual([r["company_name"] for r in rows], ["A", "B"])
        self.assertEqual(rows[0]["cold_email_draft"], "Hi,\n\nBest")
        self.assertEqual(rows[1]["cold_email_draft"], "")
        self.assertEqual(rows[1].get("employees"), "")   # a blank input cell must not become the text "nan"


class RunPipelineTest(Base):
    """The whole run with every outside call replaced: three companies, three different outcomes."""

    def run_it(self, analyze=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        source = Path(folder.name) / "in.csv"
        source.write_text(
            "company_name,website_url,country,employees\n"
            "Brand A,https://brand-a.co.uk,UK,300\n"
            "Soft B,https://soft-b.de,DE,\n"
            "Dead C,https://dead-c.fr,FR,\n", encoding="utf-8")
        self.output = Path(folder.name) / "out" / "result.csv"

        def scrape(url, timeout=10):
            if "dead-c" in url:
                return {"text": f"Scraping failed: {SECRET}", "status": "SCRAPE_ERROR", "stockist_status": "NOT_TRIED",
                        "stockist_link": "", "stockist_preview": ""}
            return {"text": "Site text. " * 30, "status": "SUCCESS", "stockist_status": "NO_LINK_FOUND",
                    "stockist_link": "", "stockist_preview": ""}

        def fake_analyze(company, url, text):
            if company == "Soft B":
                return make_enrichment(sells_own_physical_goods=False, b2b_retail_distribution=False), "model-x"
            return make_enrichment(retailers_named=["Carrefour"], country_code="FR"), "model-x"

        contact = {"name": "Claire Roux", "first_name": "Claire", "title": "VP, Supply Chain", "email": "",
                   "status": "ok", "candidates": "VP, Supply Chain @ United Kingdom [PICKED]",
                   "location": "United Kingdom", "company": "company Brand UK"}
        captured = io.StringIO()
        patches = [
            mock.patch.object(settings, "INPUT_CSV", str(source)), mock.patch.object(settings, "OUTPUT_CSV", str(self.output)),
            mock.patch.object(settings, "PAUSE_BETWEEN_LEADS", 0), mock.patch.object(settings, "APOLLO_API_KEY", SECRET),
            mock.patch.object(p, "scrape_website", side_effect=scrape),
            mock.patch.object(p, "web_search", return_value=[]),
            mock.patch("leadrouter.retail.web_search", return_value=[]),
            mock.patch.object(p, "analyze_lead", side_effect=analyze or fake_analyze),
            mock.patch.object(p, "find_contact", return_value=dict(contact)),
            mock.patch.object(p, "generate_email", return_value={"subject": "Adding retailers at Brand A", "body": "Hi Claire,\n\nBest", "flags": []}),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(captured):
                rows = p.run_pipeline()
        return rows, captured.getvalue()

    def test_three_outcomes_one_consistent_file(self):
        rows, _ = self.run_it()
        by_company = {r["company_name"]: r for r in rows}
        self.assertTrue(by_company["Brand A"]["routing_status"].startswith("Tier 1"))
        self.assertEqual(by_company["Brand A"]["contact_name"], "Claire Roux")
        self.assertEqual(by_company["Brand A"]["email_subject"], "Adding retailers at Brand A")
        self.assertTrue(by_company["Soft B"]["routing_status"].startswith("Tier 3"))
        self.assertEqual(by_company["Soft B"]["contact_name"], "")        # no contact, no email for a Tier 3
        self.assertEqual(by_company["Soft B"]["cold_email_draft"], "")
        self.assertEqual(by_company["Dead C"]["routing_status"], "To check - website unreadable")

        with open(self.output, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            written = list(reader)
            header = reader.fieldnames
        self.assertEqual(len(written), 3)
        self.assertEqual(header[:4], ["company_name", "website_url", "country", "employees"])
        self.assertTrue(set(p.OUTPUT_COLUMNS) <= set(header))

    def test_an_unreadable_site_is_scored_from_search_results_but_never_tier_one(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        source = Path(folder.name) / "in.csv"
        source.write_text("company_name,website_url,country,employees\nBlocked Co,https://blocked.co.uk,UK,300\n", encoding="utf-8")
        seen = {}

        def analyze(company, url, text):
            seen["evidence"] = text
            return make_enrichment(retailers_named=["Carrefour", "Monoprix", "Leclerc"]), "model-x"

        results = [{"title": "Where to buy", "url": "https://shop.example/blocked",
                    "content": "Blocked Co biscuits are sold at Carrefour, Monoprix and Leclerc."}]
        blocked = {"text": "Scraping failed: 403", "status": "SCRAPE_ERROR", "stockist_status": "NOT_TRIED",
                   "stockist_link": "", "stockist_preview": ""}
        patches = [
            mock.patch.object(settings, "INPUT_CSV", str(source)),
            mock.patch.object(settings, "OUTPUT_CSV", str(Path(folder.name) / "out.csv")),
            mock.patch.object(settings, "PAUSE_BETWEEN_LEADS", 0),
            mock.patch.object(p, "scrape_website", return_value=blocked),
            mock.patch.object(p, "web_search", return_value=results),
            mock.patch("leadrouter.retail.web_search", return_value=[]),
            mock.patch.object(p, "analyze_lead", side_effect=analyze),
            mock.patch.object(p, "find_contact") , mock.patch.object(p, "generate_email", return_value={"subject": "s", "body": "b", "flags": []}),
        ]
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(patch) for patch in patches]
            with contextlib.redirect_stdout(io.StringIO()):
                rows = p.run_pipeline()
        row = rows[0]
        self.assertNotIn("Scraping failed", seen["evidence"])         # the error message is not evidence
        self.assertIn("Carrefour", seen["evidence"])                  # the search results are
        self.assertTrue(row["routing_status"].startswith("Tier 2"))   # the maximum of points, held back
        self.assertEqual(row["score"], 10)
        self.assertIn("Website unreadable", row["score_breakdown"])
        self.assertEqual(row["scrape_status"], "SCRAPE_ERROR")
        mocks[7].assert_not_called()                                  # no contact is searched for a Tier 2

    def test_nothing_secret_reaches_the_terminal_or_the_file(self):
        _, printed = self.run_it()
        self.assertNotIn(SECRET, printed)
        self.assertNotIn(SECRET, self.output.read_text(encoding="utf-8"))

    def test_a_model_failure_on_one_company_does_not_stop_the_others(self):
        def analyze(company, url, text):
            if company == "Brand A":
                raise RuntimeError(f"model down, key {SECRET}")
            return make_enrichment(sells_own_physical_goods=False), "model-x"
        rows, printed = self.run_it(analyze)
        self.assertEqual(rows[0]["routing_status"], "Error - to check")
        self.assertNotIn(SECRET, rows[0]["error"])
        self.assertNotIn(SECRET, printed)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()