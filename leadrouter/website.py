"""Finding a company's official website when the CSV has none.

Three layers, so that "Netflix Foods" does not end up on netflix.com:
  1. the result must not be a social network, a database, a marketplace...
  2. the company name must be in the domain or on the page (connectors "et", "&", "and" are ignored)
  3. a model reads the page and confirms it belongs to THIS company, not a bigger one sharing a word
"""

import itertools
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .llm import check_website_identity
from .textutils import CONNECTOR_WORDS, DESCRIPTOR_WORDS, compact, core_name, name_words
from .web import fetch, web_search


# Sites that are never a company's own website
NOT_A_COMPANY_SITE = {
    "wikipedia", "wikidata", "linkedin", "facebook", "instagram", "twitter", "x", "youtube", "tiktok",
    "pinterest", "reddit", "amazon", "ebay", "crunchbase", "pitchbook", "dealroom", "glassdoor", "indeed",
    "societe", "pappers", "trustpilot", "bloomberg", "reuters", "lesechos", "maddyness",
}


SECOND_LEVEL_LABELS = {"co", "com", "org", "net", "gov", "ac"}


def is_company_site(host: str) -> bool:
    parts = host.replace("www.", "").split(".")
    return not any(p in NOT_A_COMPANY_SITE for p in parts[:-1])


def slug_variants(company_name: str, include_short: bool = True) -> set[str]:
    """Every way a domain could spell the name: connector dropped or written out, descriptor kept or not.
    With include_short=False the descriptor is always kept, so only domains spelling the FULL name match."""
    words = name_words(company_name)
    variants = set()
    for drop_descriptors in ((False, True) if include_short else (False,)):
        kept = [w for w in words if not (drop_descriptors and w in DESCRIPTOR_WORDS)]
        options = [["", "et", "and", "und"] if (w == "&" or w in CONNECTOR_WORDS) else [w] for w in kept]
        if len(options) > 8:
            continue
        for combo in itertools.product(*options):
            variants.add("".join(combo))
    return {v for v in variants if v}


def host_slug(host: str) -> str:
    labels = host.replace("www.", "").split(".")
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL_LABELS:
        slug = labels[-3]
    elif len(labels) >= 2:
        slug = labels[-2]
    else:
        slug = labels[0]
    return slug.replace("-", "")


def page_identity_text(url: str) -> str:
    """Title, site name, description, main heading and the start and end of the page text."""
    soup = BeautifulSoup(fetch(url, 10), "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    site_name_tag = soup.find("meta", attrs={"property": "og:site_name"})
    site_name = site_name_tag.get("content", "").strip() if site_name_tag else ""
    desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    description = desc_tag.get("content", "").strip() if desc_tag else ""
    h1 = soup.find("h1")
    heading = h1.get_text(" ", strip=True) if h1 else ""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    body = " ".join(soup.stripped_strings)
    return (
        f"Title: {title}\nSite name: {site_name}\nDescription: {description}\n"
        f"Main heading: {heading}\nStart of text: {body[:700]}\nEnd of text: {body[-300:]}"
    )


def find_website(company_name: str, country: str) -> tuple[str, str, str]:
    """Returns (url, confidence, note). Confidence is 'high' only if the name is found AND the model
    confirms the page belongs to this company; 'medium' if the page cannot be read but the domain spells the full
    name; otherwise 'low' (best candidate kept for a manual check)."""
    results = web_search(f"{company_name} official website", country)
    variants = slug_variants(company_name)
    full_variants = slug_variants(company_name, include_short=False)
    core = core_name(company_name)

    candidates, seen_hosts = [], set()
    for res in results:
        parsed = urlparse(res.get("url", ""))
        host = parsed.netloc.lower()
        if not host or host in seen_hosts or not is_company_site(host):
            continue
        seen_hosts.add(host)
        candidates.append((host_slug(host) in variants, host_slug(host) in full_variants,
                           f"{parsed.scheme or 'https'}://{host}"))
    if not candidates:
        return "", "", "no usable search result"

    # Domains that spell the name exactly come first. Without a known country, the .com (the brand's main
    # site) comes before a regional one such as .us or .fr; with a country, the search order is kept.
    def rank(candidate):
        exact_domain, _, root = candidate
        regional = 0 if (country or root.endswith(".com")) else 1
        return (not exact_domain, regional)
    candidates.sort(key=rank)
    notes = []  # what happened with each candidate, so a rejection is fully explained
    for exact_domain, exact_full, root in candidates[:3]:
        try:
            identity = page_identity_text(root)
        except Exception:
            if exact_full:
                # The page blocks us, but the domain spells the full name. Not proof (so only 'medium'), yet far
                # stronger than a partial match: 'cawstonpress.com' for "Cawston Press", not 'netflix.com' for
                # "Netflix Foods".
                return root, "medium", f"{root}: the domain spells the full name, but the page could not be read"
            notes.append(f"{root} could not be read")
            continue
        name_found = exact_domain or (len(core) >= 4 and core in compact(identity))
        if not name_found:
            notes.append(f"{root}: company name not found on the page")
            continue
        is_official, reason = check_website_identity(company_name, country, root, identity)
        if is_official:
            return root, "high", reason
        notes.append(f"{root}: {reason}")
    return candidates[0][2], "low", " | ".join(notes)