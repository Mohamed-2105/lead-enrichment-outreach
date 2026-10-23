import unittest
from unittest import mock

from leadrouter import retail, settings
from tests.helpers import Base, make_enrichment, response


class VerifyTest(unittest.TestCase):
    def test_invented_retailers_are_dropped(self):
        text = "Disponible chez Carrefour, Monoprix et Leclerc."
        verified = retail.verify_retailers(["Carrefour", "monoprix", "Tesco", "ab"], text, "Brand")
        self.assertEqual(verified, ["Carrefour", "monoprix"])

    def test_the_brands_own_name_is_not_a_retailer(self):
        self.assertEqual(retail.verify_retailers(["Brand shop"], "Visit the Brand shop", "Brand"), [])

    def test_headcount_must_appear_in_the_text(self):
        text = "The company has 1,200 employees."
        self.assertEqual(retail.verify_employees(1200, text), 1200)
        self.assertIsNone(retail.verify_employees(120, text))     # 120 is not 1,200
        self.assertIsNone(retail.verify_employees(0, text))

    def test_chain_hits_need_the_brand_and_a_known_chain_domain(self):
        results = [
            {"title": "Cookies Alba et Bruno", "url": "https://www.carrefour.fr/p/cookies-alba-et-bruno-1", "content": ""},
            {"title": "Autre marque", "url": "https://www.auchan.fr/p/autre-456", "content": "sans rapport"},
            {"title": "Blog Alba et Bruno", "url": "https://blog.example.com/alba-et-bruno", "content": ""},
        ]
        hits = retail.retailer_domain_hits("Alba et Bruno", "FR", results)
        self.assertEqual([name for name, _ in hits], ["Carrefour"])

    def test_chain_query_depends_on_the_country(self):
        self.assertIn("site:rewe.de", retail.retailer_site_query("Zanto", "DE"))
        self.assertIn("site:tesco.com", retail.retailer_site_query("Zanto", "UK"))
        self.assertEqual(retail.retailer_site_query("Zanto", "JP"), "")


class ApolloHeadcountTest(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(settings, "APOLLO_API_KEY", "key-for-tests-123")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_not_configured(self):
        with mock.patch.object(settings, "APOLLO_API_KEY", ""):
            self.assertEqual(retail.apollo_employees("a.com")[0], None)
            self.assertIn("not_configured", retail.apollo_employees("a.com")[1])

    def test_ok_and_no_match(self):
        with mock.patch("requests.get", return_value=response(organization={"estimated_num_employees": 340})):
            self.assertEqual(retail.apollo_employees("a.com"), (340, "ok"))
        with mock.patch("requests.get", return_value=response(organization=None)):
            self.assertIn("no_match", retail.apollo_employees("zzz.com")[1])

    def test_a_refusal_is_reported_and_stops_further_calls(self):
        with mock.patch("requests.get", return_value=response(403, text='{"error":"master key needed"}')) as get:
            first = retail.apollo_employees("a.com")
            second = retail.apollo_employees("b.com")
        self.assertIn("error 403", first[1])
        self.assertIn("skipped after access error", second[1])
        self.assertEqual(get.call_count, 1)

    def test_headcount_order_of_trust(self):
        evidence = "About 80 employees."
        # 1. typed in the CSV: Apollo is not called
        with mock.patch("requests.get") as get:
            self.assertEqual(retail.resolve_headcount({"employees": "300"}, make_enrichment(), evidence)[:2], (300, "manual"))
            get.assert_not_called()
        # 2. Apollo
        with mock.patch("requests.get", return_value=response(organization={"estimated_num_employees": 340})):
            self.assertEqual(retail.resolve_headcount({"clean_domain": "a.com"}, make_enrichment(), evidence)[:2], (340, "apollo"))
        # 3. a number stated in the evidence
        with mock.patch("requests.get", return_value=response(organization=None)):
            result = retail.resolve_headcount({"clean_domain": "a.com"}, make_enrichment(employees_estimate=80), evidence)
        self.assertEqual(result[:2], (80, "evidence"))
        # software company: Apollo is not even called
        with mock.patch("requests.get") as get:
            _, _, status = retail.resolve_headcount({"clean_domain": "a.com"}, make_enrichment(sells_own_physical_goods=False), "")
            get.assert_not_called()
        self.assertIn("not a physical-goods brand", status)


class CollectRetailersTest(Base):
    def test_text_and_chain_proofs_are_merged_without_duplicates(self):
        enrichment = make_enrichment(retailers_named=["Monoprix", "Carrefour", "Tesco"])
        evidence = "Biscuits sold at Monoprix."
        chain = [{"title": "Brand - Carrefour", "url": "https://www.carrefour.fr/p/brand-1", "content": ""}]
        with mock.patch.object(retail, "web_search", return_value=chain):
            verified, sources, _ = retail.collect_retailers("Brand", "FR", enrichment, evidence, [], [])
        self.assertEqual(sorted(verified), ["Carrefour", "Monoprix"])   # Tesco was never proven
        self.assertIn("Monoprix (brand site)", sources)
        self.assertIn("https://www.carrefour.fr/p/brand-1", sources)


if __name__ == "__main__":
    unittest.main()