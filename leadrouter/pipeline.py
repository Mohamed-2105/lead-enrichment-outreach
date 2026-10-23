"""The pipeline: reads the CSV, processes each company one by one and saves the result after every company,
so a crash never loses the work already done.

For one company: read the website -> search the web -> ask the model for facts -> verify them with plain rules
-> score -> find a contact -> write the email. Each step is a small function below."""

import csv
import time
from collections import Counter
from pathlib import Path

import pandas as pd

from . import settings
from .contacts import empty_contact, find_contact
from .email_writer import generate_email
from .llm import analyze_lead
from .log import log
from .retail import APOLLO_BLOCKED, APOLLO_STATS, collect_retailers, resolve_headcount
from .scoring import MAX_SCORE, score_and_route
from .textutils import build_search_link, clean_country, compact, normalize_domain, tld_country
from .web import format_results, scrape_website, web_search
from .website import find_website

# Every output column, in order. A row always has all of them (blank when not applicable).
OUTPUT_COLUMNS = [
    "routing_status", "score", "score_breakdown", "error",
    "country_used", "country_source", "employees_used", "employees_source", "apollo_status",
    "detected_industry", "sells_own_physical_goods", "b2b_retail_distribution", "company_summary",
    "retailers_named_raw", "retailers_verified", "retailers_verified_count", "retailers_verified_sources",
    "search_results_count", "scrape_status", "stockist_page_status", "stockist_link", "stockist_text_preview",
    "contact_name", "contact_title", "contact_email", "contact_status", "contact_candidates",
    "contact_location", "contact_company",
    "email_subject", "cold_email_draft", "email_flags", "model_used", "search_link",
]
BLANK_FIELDS = {column: "" for column in OUTPUT_COLUMNS}

# Stand-in for a website that was never read
NO_SCRAPE = {"text": "", "status": "NO_WEBSITE", "stockist_status": "NOT_TRIED", "stockist_link": "",
             "stockist_preview": ""}


# ---------------------------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------------------------
def build_row(row: dict, **values) -> dict:
    """The input columns, then every output column (blank unless given). All rows share the same columns."""
    unknown = set(values) - set(OUTPUT_COLUMNS)
    if unknown:
        raise KeyError(f"Not an output column: {sorted(unknown)}")
    return {**row, **BLANK_FIELDS, **values}


def unscored_row(row: dict, scrape: dict, status_label: str, error: str = "") -> dict:
    """A lead we could not score reliably. It stays in the output, flagged for a manual check."""
    country = clean_country(row.get("country"))
    return build_row(
        row,
        routing_status=status_label,
        error=settings.redact(error),
        scrape_status=scrape["status"],
        stockist_page_status=scrape["stockist_status"],
        stockist_link=scrape["stockist_link"],
        stockist_text_preview=scrape["stockist_preview"],
        country_used=country,
        country_source="csv" if country else "",
        search_link=build_search_link(row.get("company_name", ""), country),
    )


# ---------------------------------------------------------------------------------------------
# Preparing the list
# ---------------------------------------------------------------------------------------------
def dedupe_by_name(df: pd.DataFrame) -> pd.DataFrame:
    """The same company written two ways ('Michel et Augustin' / 'Michel & Augustin') is kept once,
    with the most complete row. Uses the full name, so 'Apple' and 'Apple Cosmetics' stay separate."""
    keys = df["company_name"].astype(str).apply(compact)
    keys = [k if k else f"__{i}" for i, k in zip(df.index, keys)]
    info = pd.Series(0, index=df.index)
    for col in ("website_url", "country", "employees"):
        if col in df.columns:
            info += df[col].apply(lambda v: 0 if str(v).strip().lower() in {"", "nan", "none"} else 1)
    ranking = pd.DataFrame({"key": keys, "info": info, "pos": range(len(df))}, index=df.index)
    keep = ranking.sort_values(["info", "pos"], ascending=[False, True]).drop_duplicates("key").index
    removed = df.loc[~df.index.isin(keep), "company_name"].tolist()
    if removed:
        log(f"Same company written twice, merged: {', '.join(removed)}")
    return df.loc[sorted(keep)].copy()


def resolve_websites(df: pd.DataFrame) -> None:
    """Companies without a website in the CSV: find it with a web search (see website.py). Edits df in place."""
    for idx in df.index[df["website_url"] == ""]:
        company = df.at[idx, "company_name"]
        country = clean_country(df.at[idx, "country"]) if "country" in df.columns else ""
        found_url, confidence, note = find_website(company, country)
        df.at[idx, "website_url"] = found_url
        df.at[idx, "website_source"] = "search" if found_url else ""
        df.at[idx, "website_confidence"] = confidence
        df.at[idx, "website_note"] = note
        log(f"Website for {company}: {found_url or 'not found'} ({confidence or 'no result'}) {note}")


def prepare_leads(path: str) -> pd.DataFrame:
    """Reads the CSV (only company_name is required) and returns one clean row per company."""
    df = pd.read_csv(path)
    if "company_name" not in df.columns:
        raise ValueError("The CSV needs a company_name column.")
    if "website_url" not in df.columns:
        df["website_url"] = ""
    df["website_url"] = df["website_url"].fillna("").astype(str).str.strip()
    df["website_source"] = df["website_url"].apply(lambda u: "csv" if u else "")
    df["website_confidence"] = df["website_url"].apply(lambda u: "given" if u else "")
    df["website_note"] = ""
    df = dedupe_by_name(df)

    if settings.MAX_LEADS > 0:
        df = df.head(settings.MAX_LEADS)
        log(f"TEST MODE: only the first {settings.MAX_LEADS} companies (MAX_LEADS in .env)")

    resolve_websites(df)
    df["clean_domain"] = [
        normalize_domain(u) if u else f"no-website-{i}" for i, u in zip(df.index, df["website_url"])
    ]
    return df.drop_duplicates(subset=["clean_domain"]).copy()


# ---------------------------------------------------------------------------------------------
# One company, step by step
# ---------------------------------------------------------------------------------------------
def gather_evidence(company: str, scrape: dict, country_hint: str) -> tuple[str, list, list]:
    """The website text plus two web searches (where it is sold, how big it is), as one text for the model."""
    retail_results = web_search(f"{company} where to buy stockists retailers", country_hint)
    size_results = web_search(f"{company} number of employees", country_hint)
    evidence = (
        scrape["text"]
        + format_results("Web search results about where the brand is sold", retail_results)
        + format_results("Web search results about company size", size_results)
    )
    return evidence, retail_results, size_results


def decide_country(given: str, url: str, model_code: str) -> tuple[str, str]:
    """The CSV first, then the website extension, then what the model read on the site."""
    if given:
        return given, "csv"
    if tld_country(url):
        return tld_country(url), "domain"
    if clean_country(model_code):
        return clean_country(model_code), "model"
    return "", ""


def lookup_contact(row: dict, routing: dict, company: str) -> dict:
    """Only the best leads (CONTACT_TIERS) get a contact."""
    if not routing["routing_status"].startswith(settings.CONTACT_TIERS):
        return empty_contact()
    contact = find_contact(row.get("clean_domain", ""), company)
    if contact["name"]:
        log(f"    contact: {contact['name']}, {contact['title']} ({contact['location']}, {contact['company']}) "
            f"[{contact['status']}]")
    else:
        log(f"    contact: none [{contact['status']}]")
    if contact["candidates"]:
        log(f"    candidates: {contact['candidates']}")
    return contact


def write_email(company: str, country: str, enrichment, verified: list, routing: dict, contact: dict) -> dict:
    """Only the leads in EMAIL_TIERS get an email. A failure never stops the run: it is flagged in the row."""
    email = {"subject": "", "body": "", "flags": []}
    if not routing["routing_status"].startswith(settings.EMAIL_TIERS):
        return email
    try:
        email = generate_email(company, country, enrichment.company_summary, verified,
                               contact_first_name=contact["first_name"])
    except Exception as e:
        email["flags"] = [f"email generation failed: {settings.redact(e)[:120]}"]
    if email["flags"]:
        log(f"    ! email flagged: {' | '.join(email['flags'])}")
    return email


def process_lead(row: dict, position: int, total: int) -> dict:
    """Everything for one company. Always returns one output row, whatever happens."""
    company, url = row["company_name"], row["website_url"]
    given_country = clean_country(row.get("country"))
    log(f"[{position}/{total}] {company}")

    if not url:
        log("  -> no website found, flagged 'To check' (not scored)\n")
        return unscored_row(row, NO_SCRAPE, "To check - no website found")
    if row.get("website_confidence") == "low":
        note = str(row.get("website_note", ""))
        log(f"  -> website {url} not confirmed ({note}), flagged 'To check' (not scored)\n")
        return unscored_row(row, NO_SCRAPE, "To check - website not confirmed", error=note[:300])

    scrape = scrape_website(url)
    site_read = scrape["status"] == "SUCCESS"
    site = scrape if site_read else {**scrape, "text": ""}   # an error message is not evidence
    evidence, retail_results, size_results = gather_evidence(company, site, given_country or tld_country(url))
    if not site_read:
        if not (retail_results or size_results):
            log(f"  -> {scrape['status']} and no search results, flagged 'To check' (not scored)\n")
            return unscored_row(row, scrape, "To check - website unreadable", error=scrape["text"][:200])
        log(f"  -> {scrape['status']}: scoring from web search results only (Tier 2 at most)")
    try:
        enrichment, model_used = analyze_lead(company, url, evidence)
    except Exception as e:
        log(f"  -> model error, flagged 'Error' ({e})\n")
        return unscored_row(row, scrape, "Error - to check", error=str(e)[:300])

    country, country_source = decide_country(given_country, url, enrichment.country_code)
    verified, sources, chain_results = collect_retailers(
        company, country, enrichment, evidence, retail_results, size_results)
    employees, employees_source, apollo_status = resolve_headcount(row, enrichment, evidence)
    routing = score_and_route(enrichment, country, company, verified, employees=employees, site_read=site_read)
    contact = lookup_contact(row, routing, company)
    email = write_email(company, country, enrichment, verified, routing, contact)

    log(f"  -> Score {routing['score']}/{MAX_SCORE} => {routing['routing_status']} "
        f"({len(verified)} retailers verified, stockist page: {scrape['stockist_status']}, {model_used})\n")

    return build_row(
        row,
        routing_status=routing["routing_status"], score=routing["score"], score_breakdown=routing["score_breakdown"],
        country_used=country, country_source=country_source,
        employees_used=employees if employees is not None else "", employees_source=employees_source,
        apollo_status=apollo_status,
        detected_industry=enrichment.industry, sells_own_physical_goods=enrichment.sells_own_physical_goods,
        b2b_retail_distribution=enrichment.b2b_retail_distribution, company_summary=enrichment.company_summary,
        retailers_named_raw=" | ".join(enrichment.retailers_named), retailers_verified=" | ".join(verified),
        retailers_verified_count=len(verified), retailers_verified_sources=sources,
        search_results_count=len(retail_results) + len(chain_results) + len(size_results),
        scrape_status=scrape["status"], stockist_page_status=scrape["stockist_status"],
        stockist_link=scrape["stockist_link"], stockist_text_preview=scrape["stockist_preview"],
        contact_name=contact["name"], contact_title=contact["title"], contact_email=contact["email"],
        contact_status=contact["status"], contact_candidates=contact["candidates"],
        contact_location=contact["location"], contact_company=contact["company"],
        email_subject=email["subject"], cold_email_draft=email["body"], email_flags=" | ".join(email["flags"]),
        model_used=model_used, search_link=build_search_link(company, country),
    )


# ---------------------------------------------------------------------------------------------
# Output and summary
# ---------------------------------------------------------------------------------------------
def is_blank(value) -> bool:
    """None and NaN (a blank cell read by pandas) are written as an empty cell, not as the text 'nan'."""
    return value is None or (isinstance(value, float) and value != value)


def append_row(path: str, row: dict, first: bool) -> None:
    """Writes one row at the end of the output file (the file is started again when `first`)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w" if first else "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()), extrasaction="ignore")
        if first:
            writer.writeheader()
        writer.writerow({key: ("" if is_blank(value) else value) for key, value in row.items()})


def summarize(rows: list[dict]) -> None:
    log(f"Done. Results in {settings.OUTPUT_CSV}")
    for status, count in Counter(r["routing_status"] for r in rows).most_common():
        log(f"  {count} x {status}")
    asked = [r for r in rows if str(r["routing_status"]).startswith(settings.CONTACT_TIERS)]
    if asked:
        found = sum(1 for r in asked if r["contact_name"])
        log(f"Contacts found for {found} of {len(asked)} lead(s) in {', '.join(settings.CONTACT_TIERS)}")
    if APOLLO_STATS:
        log("Apollo summary: " + ", ".join(f"{k} x{v}" for k, v in APOLLO_STATS.items()))
        if APOLLO_BLOCKED["reason"]:
            log(f"Apollo was refused: {APOLLO_BLOCKED['reason']}")


def run_pipeline() -> list[dict]:
    """Runs the whole list and returns the output rows (also saved to settings.OUTPUT_CSV)."""
    df = prepare_leads(settings.INPUT_CSV)
    total = len(df)
    log(f"Starting pipeline: {settings.PRIMARY_MODEL} (fallback {settings.FALLBACK_MODEL}) on {total} companies\n")

    rows = []
    for position, row in enumerate(df.to_dict("records"), start=1):
        result = process_lead(row, position, total)
        rows.append(result)
        append_row(settings.OUTPUT_CSV, result, first=position == 1)   # saved after every company
        if result["score"] != "":
            time.sleep(settings.PAUSE_BETWEEN_LEADS)   # free accounts have per-minute limits

    summarize(rows)
    return rows