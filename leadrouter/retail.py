"""Evidence about distribution and size: which retailers sell the brand, and how big the company is."""

import re
from collections import Counter
from urllib.parse import urlparse

import requests

from . import settings
from .log import log
from .textutils import normalize_text, parse_employees
from .web import web_search

# What happened with Apollo during this run (shown in the final summary)
APOLLO_STATS: Counter = Counter()
APOLLO_BLOCKED = {"reason": ""}


def retailer_domains_for(country: str) -> dict:
    code = str(country).strip().upper()
    return settings.RETAILER_DOMAINS.get("GB" if code == "UK" else code, {})


def retailer_site_query(company_name: str, country: str) -> str:
    """One Google query restricted to the big chains of the country: "Brand" site:a OR site:b ..."""
    domains = retailer_domains_for(country)
    if not domains:
        return ""
    return f'"{company_name}" ' + " OR ".join(f"site:{d}" for d in domains)


def retailer_domain_hits(company_name: str, country: str, results: list[dict]) -> list[tuple[str, str]]:
    """(retailer name, url) for each result that sits on a known chain's domain AND mentions the brand."""
    domains = retailer_domains_for(country)
    brand = normalize_text(company_name)
    hits, seen = [], set()
    for res in results:
        url = res.get("url", "")
        host = urlparse(url).netloc.lower()
        blob = normalize_text(
            f"{res.get('title', '')} {res.get('content', '')} {url.replace('-', ' ').replace('_', ' ')}"
        )
        for fragment, name in domains.items():
            if (host == fragment or host.endswith("." + fragment) or fragment in host) and brand in blob:
                if name not in seen:
                    seen.add(name)
                    hits.append((name, url))
    return hits


def retailer_sources(verified: list[str], results: list[dict]) -> str:
    """For each verified retailer, the first search result that mentions it (or 'brand site')."""
    out = []
    for name in verified:
        key = normalize_text(name)
        for res in results:
            blob = normalize_text(f"{res.get('title', '')} {res.get('content', '')}")
            if key in blob:
                out.append(f"{name} ({res.get('url', '')})")
                break
        else:
            out.append(f"{name} (brand site)")
    return " | ".join(out)


def verify_retailers(names: list[str], scraped_text: str, company_name: str) -> list[str]:
    """Keeps only the retailer names that really appear in the scraped text.
    This is the check that stops the model from inventing retail partners."""
    text = normalize_text(scraped_text)
    own_name = normalize_text(company_name)
    verified, seen = [], set()
    for name in names:
        key = normalize_text(name)
        if len(key) < 3 or key in seen or own_name in key:
            continue
        if key in text:
            seen.add(key)
            verified.append(name.strip())
    return verified


def verify_employees(n: int, text: str) -> int | None:
    """Keeps the headcount only if the number really appears in the evidence text."""
    if not n or n <= 0:
        return None
    flat = re.sub(r"(?<=\d)[,.\s](?=\d{3}(?!\d))", "", text)
    return n if re.search(rf"(?<!\d){n}(?!\d)", flat) else None


def apollo_employees(domain: str) -> tuple[int | None, str]:
    """Headcount from Apollo's organization enrichment. Returns (value, status).
    The status always says what happened: ok, no_match, not_configured, or the exact error."""
    if not settings.APOLLO_API_KEY:
        return None, "not_configured (no APOLLO_API_KEY)"
    if not domain:
        return None, "no_domain"
    if APOLLO_BLOCKED["reason"]:
        return None, f"skipped after access error: {APOLLO_BLOCKED['reason']}"
    try:
        response = requests.get(
            "https://api.apollo.io/api/v1/organizations/enrich",
            headers={"x-api-key": settings.APOLLO_API_KEY, "Cache-Control": "no-cache"},
            params={"domain": domain},
            timeout=15,
        )
        if response.status_code in (401, 403):
            body = " ".join(response.text.split())[:250]
            APOLLO_BLOCKED["reason"] = f"{response.status_code} {body}"
            log(f"    !! APOLLO REFUSED THE REQUEST ({response.status_code}): {body}")
            log("    !! Apollo will not be called again in this run. Fix the key or the plan, then relaunch.")
            return None, f"error {response.status_code}: {body}"
        response.raise_for_status()
        organization = response.json().get("organization") or {}
        value = organization.get("estimated_num_employees")
        if value:
            return int(value), "ok"
        return None, "no_match (Apollo has no headcount for this domain)"
    except Exception as e:
        log(f"    ! Apollo lookup failed: {e}")
        return None, f"error: {str(e)[:200]}"


def collect_retailers(company, country, enrichment, evidence_text, retail_results, size_results):
    """The retailers proven for this brand, and where each proof comes from.

    Two kinds of proof: a retailer name the model read in the evidence (and that really appears in it), and a page
    on a big chain's own website that mentions the brand. Returns (verified names, sources text, chain results)."""
    chain_query = retailer_site_query(company, country)
    chain_results = web_search(chain_query, country) if chain_query else []
    chain_hits = retailer_domain_hits(company, country, chain_results)

    verified = verify_retailers(enrichment.retailers_named, evidence_text, company)
    text_sources = retailer_sources(verified, retail_results + size_results)

    known = {normalize_text(v) for v in verified}
    chain_sources = []
    for name, hit_url in chain_hits:
        chain_sources.append(f"{name} ({hit_url})")
        if normalize_text(name) not in known:
            verified.append(name)
            known.add(normalize_text(name))
    sources = " | ".join(x for x in [text_sources, " | ".join(chain_sources)] if x)
    return verified, sources, chain_results


def resolve_headcount(row, enrichment, evidence_text):
    """Headcount, in order of trust: typed in the CSV, then Apollo, then a number stated in the evidence.
    Returns (employees or None, source, apollo_status)."""
    employees = parse_employees(row.get("employees"))
    source = "manual" if employees is not None else ""
    if employees is not None:
        apollo_status = "not called (headcount typed in the CSV)"
    elif not enrichment.sells_own_physical_goods:
        apollo_status = "not called (not a physical-goods brand)"
    else:
        employees, apollo_status = apollo_employees(row.get("clean_domain", ""))
        source = "apollo" if employees is not None else ""
    APOLLO_STATS[apollo_status.split("(")[0].split(":")[0].strip()] += 1
    if employees is None:
        employees = verify_employees(enrichment.employees_estimate, evidence_text)
        source = "evidence" if employees is not None else ""
    return employees, source, apollo_status
