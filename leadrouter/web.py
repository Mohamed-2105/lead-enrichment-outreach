"""Reading websites and searching the web (Serper). Every function returns plain data and never raises on a
network problem: failures come back as a status the pipeline can show."""

import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import settings
from .log import log
from .textutils import normalize_domain


def fetch(url: str, timeout: int = 10) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    last_error = None
    for attempt in range(2):  # one retry: a first failure is often a temporary timeout
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as e:
            last_error = e
            time.sleep(1)
    raise last_error


def clean_soup(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "svg", "footer", "nav"]):
        tag.decompose()


def find_stockist_link(soup: BeautifulSoup, base_url: str) -> str | None:
    """Looks for a 'where to buy' style link on the same site. Must run BEFORE clean_soup,
    because these links are often in the menu or the footer."""
    base_domain = normalize_domain(base_url)
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        if any(keyword in label for keyword in settings.STOCKIST_KEYWORDS):
            link = urljoin(base_url, a["href"])
            if link.startswith("http") and normalize_domain(link) == base_domain:
                return link
    return None


def scrape_website(url: str, timeout: int = 10) -> dict:
    """Returns text, status (SUCCESS / SCRAPE_THIN / SCRAPE_ERROR), stockist_status and the link used."""
    try:
        html = fetch(url, timeout)
    except Exception as e:
        return {"text": f"Scraping failed: {e}", "status": "SCRAPE_ERROR",
                "stockist_status": "NOT_TRIED", "stockist_link": "", "stockist_preview": ""}

    soup = BeautifulSoup(html, "html.parser")
    stockist_link = find_stockist_link(soup, url)

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta_tag and meta_tag.get("content"):
        meta_desc = meta_tag["content"].strip()

    clean_soup(soup)
    body_text = " ".join(soup.stripped_strings)
    text = f"Title: {title}\nMeta Description: {meta_desc}\nBody: {body_text[:settings.HOME_CHARS]}"
    status = "SUCCESS" if len(title) + len(meta_desc) + len(body_text) >= settings.MIN_TEXT_LENGTH else "SCRAPE_THIN"

    stockist_status = "NO_LINK_FOUND"
    stockist_preview = ""
    if stockist_link:
        try:
            stockist_soup = BeautifulSoup(fetch(stockist_link, timeout), "html.parser")
            clean_soup(stockist_soup)
            stockist_text = " ".join(stockist_soup.stripped_strings)[:settings.STOCKIST_CHARS]
            stockist_preview = stockist_text[:300]
            if len(stockist_text) >= settings.MIN_STOCKIST_CHARS:
                stockist_status = "SUCCESS"
                text += f"\n\nWhere-to-buy / stockist page ({stockist_link}):\n{stockist_text}"
            else:
                stockist_status = "THIN"  # often a JavaScript map with no readable text
        except Exception:
            stockist_status = "ERROR"

    return {"text": text, "status": status, "stockist_status": stockist_status,
            "stockist_link": stockist_link or "", "stockist_preview": stockist_preview}


def web_search(query: str, country: str = "") -> list[dict]:
    """Google results through Serper, as a list of {title, url, content}. Empty if no key or on error."""
    if not settings.SERPER_API_KEY:
        return []
    payload = {"q": query, "num": settings.SEARCH_RESULTS}
    code = str(country).strip().lower()
    if len(code) == 2 and code.isalpha():
        payload["gl"] = "gb" if code == "uk" else code
    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": settings.SERPER_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        organic = response.json().get("organic", [])
        return [
            {"title": o.get("title", ""), "url": o.get("link", ""), "content": o.get("snippet", "")}
            for o in organic[:settings.SEARCH_RESULTS]
        ]
    except Exception as e:
        log(f"    ! web search failed: {e}")
        return []


def format_results(label: str, results: list[dict]) -> str:
    if not results:
        return ""
    lines = [f"\n\n{label}:"]
    for i, res in enumerate(results, start=1):
        content = str(res.get("content", ""))[:settings.SEARCH_CHARS_PER_RESULT]
        lines.append(f"[{i}] {res.get('title', '')} ({res.get('url', '')}): {content}")
    return "\n".join(lines)
