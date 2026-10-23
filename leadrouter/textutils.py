"""Small text helpers: names, domains, countries, headcounts. No network, no secrets."""

import re
import unicodedata
from urllib.parse import quote_plus, urlparse


# Words ignored when comparing names: 'Michel et Augustin' = 'Michel & Augustin' = 'Michel and Augustin'
CONNECTOR_WORDS = {"et", "and", "und", "y", "e"}


# Generic words a domain or a company record often drops: 'Heura Foods' -> heura.com
DESCRIPTOR_WORDS = {
    "foods", "food", "drinks", "drink", "cosmetics", "cosmetiques", "beauty", "group", "company", "co",
    "brand", "brands", "labs", "lab", "studio", "shop", "store", "official", "france", "deutschland",
    "germany", "uk", "paris", "europe", "international",
}


COUNTRY_NAMES = {
    "FRANCE": "FR", "ALLEMAGNE": "DE", "GERMANY": "DE", "DEUTSCHLAND": "DE", "UK": "GB", "UNITED KINGDOM": "GB",
    "ROYAUME-UNI": "GB", "ROYAUME UNI": "GB", "ENGLAND": "GB", "ESPAGNE": "ES", "SPAIN": "ES", "ITALIE": "IT",
    "ITALY": "IT", "PAYS-BAS": "NL", "NETHERLANDS": "NL", "SUEDE": "SE", "SUÈDE": "SE", "SWEDEN": "SE",
    "AUTRICHE": "AT", "AUSTRIA": "AT", "BELGIQUE": "BE", "BELGIUM": "BE", "USA": "US", "UNITED STATES": "US",
    "ETATS-UNIS": "US", "ÉTATS-UNIS": "US",
}


TLD_COUNTRIES = {"fr": "FR", "de": "DE", "uk": "GB", "es": "ES", "it": "IT", "nl": "NL", "se": "SE",
                 "at": "AT", "be": "BE", "us": "US"}


def normalize_domain(url: str) -> str:
    parsed = urlparse(str(url).strip().lower())
    domain = parsed.netloc or parsed.path
    return domain.replace("www.", "")


def normalize_text(text: str) -> str:
    """Lowercase, remove accents, collapse spaces. Used to check names against the page text."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def clean_country(value) -> str:
    """A two-letter country code, or '' if the value is empty, 'EU' or not understood."""
    text = "" if value is None else str(value).strip().upper()
    if text in {"", "NAN", "NONE", "NULL", "N/A", "EU"}:
        return ""
    text = COUNTRY_NAMES.get(text, text)
    text = "GB" if text == "UK" else text
    return text if len(text) == 2 and text.isalpha() else ""


def tld_country(url: str) -> str:
    """Guesses the country from the website's extension (.fr, .de, .co.uk...). '' for .com and others."""
    parsed = urlparse(str(url).strip().lower())
    host = parsed.netloc or parsed.path
    return TLD_COUNTRIES.get(host.split("/")[0].split(".")[-1], "")


def parse_employees(value) -> int | None:
    """Reads a headcount typed by hand: 120, '1,200', '51-200' (lower bound is used). None if empty."""
    if value is None:
        return None
    cleaned = str(value).replace(",", "").replace(" ", "")
    numbers = re.findall(r"\d+", cleaned)
    return int(numbers[0]) if numbers else None


def build_search_link(company_name: str, country: str) -> str:
    """A Google search to check by hand which retailers stock the brand."""
    query = quote_plus(f"{company_name} where to buy stockists {clean_country(country)}".strip())
    return f"https://www.google.com/search?q={query}"


def name_words(company_name: str) -> list[str]:
    text = normalize_text(company_name).replace("&", " & ")
    text = re.sub(r"['’]", "", text)
    return re.findall(r"&|[a-z0-9]+", text)


def compact(text: str) -> str:
    """Lowercase letters and digits only, connectors removed: 'Michel & Augustin' and 'Michel et Augustin'
    both become 'michelaugustin'."""
    text = normalize_text(text).replace("&", " ")
    text = re.sub(r"['’]", "", text)
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in CONNECTOR_WORDS]
    return "".join(words)


def core_name(company_name: str) -> str:
    """The name without connectors and generic descriptors ('Heura Foods' -> 'heura')."""
    words = [w for w in name_words(company_name) if w != "&" and w not in CONNECTOR_WORDS]
    core = [w for w in words if w not in DESCRIPTOR_WORDS]
    return "".join(core or words)


def has_word(title: str, keyword: str) -> bool:
    """Whole-word match, so 'coo' does not match 'Coordinator' and 'pr' does not match 'Procurement'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", title.lower()) is not None
