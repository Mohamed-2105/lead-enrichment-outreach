"""All settings of the lead router, in one place."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env_file() -> None:
    """Reads KEY=value lines from `.env` (project folder first, then the current folder).
    Variables already exported in the terminal win over the file."""
    for folder in (PROJECT_ROOT, Path.cwd()):
        env_path = folder / ".env"
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
        return


load_env_file()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ---------------------------------------------------------------------------------------------
# Secrets: read from the environment only. See .env.example for the list.
# ---------------------------------------------------------------------------------------------
GEMINI_API_KEY = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
SERPER_API_KEY = _env("SERPER_API_KEY")      # optional: web search (sites, retailers, headcount)
APOLLO_API_KEY = _env("APOLLO_API_KEY")      # optional: headcount and contacts
DRAFT_RECIPIENT = _env("DRAFT_RECIPIENT")    # your own address, only used by create_drafts.py

SECRET_NAMES = ("GEMINI_API_KEY", "SERPER_API_KEY", "APOLLO_API_KEY", "DRAFT_RECIPIENT")


def secret_values() -> list[str]:
    """Every secret currently set, whether it came from the environment or was assigned on this module."""
    values = []
    for name in SECRET_NAMES:
        for candidate in (os.environ.get(name, ""), globals().get(name, "")):
            if isinstance(candidate, str) and len(candidate.strip()) >= 6:
                values.append(candidate.strip())
    if _env("GOOGLE_API_KEY"):
        values.append(_env("GOOGLE_API_KEY"))
    return sorted(set(values), key=len, reverse=True)


def redact(text) -> str:
    """Hides every secret in a piece of text. All logging and CSV error fields go through this."""
    text = str(text)
    for value in secret_values():
        text = text.replace(value, "[hidden]")
    return text


def configured_services() -> dict[str, bool]:
    """Which services are configured, without ever showing a value."""
    return {
        "Gemini (required)": bool(GEMINI_API_KEY),
        "Serper (web search)": bool(SERPER_API_KEY),
        "Apollo (headcount, contacts)": bool(APOLLO_API_KEY),
    }


# ---------------------------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------------------------
INPUT_CSV = _env("LEADS_INPUT") or str(PROJECT_ROOT / "data" / "leads_input.csv")
OUTPUT_CSV = _env("LEADS_OUTPUT") or str(PROJECT_ROOT / "output" / "leads_enriched.csv")

# For a test run, put MAX_LEADS=4 in your .env: only the first 4 companies are processed (0 = all).
MAX_LEADS = int(_env("MAX_LEADS", "0") or 0)

# ---------------------------------------------------------------------------------------------
# Models and pacing
# ---------------------------------------------------------------------------------------------
PRIMARY_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODEL = "gemini-3.8-flash"
MAX_RETRIES_PER_MODEL = 2
RETRY_BASE_DELAY = 2.0
PAUSE_BETWEEN_LEADS = 4.0   # free accounts have per-minute limits

# ---------------------------------------------------------------------------------------------
# Reading websites and searching the web
# ---------------------------------------------------------------------------------------------
MIN_TEXT_LENGTH = 200       # below this, the homepage is considered unreadable
HOME_CHARS = 2000           # homepage text sent to the model
STOCKIST_CHARS = 2500       # stockist page text sent to the model
MIN_STOCKIST_CHARS = 100    # below this, the stockist page is considered empty
SEARCH_RESULTS = 6
SEARCH_CHARS_PER_RESULT = 400

# Words that identify a "where to buy" / "stockists" / "become a retailer" link
STOCKIST_KEYWORDS = [
    "where to buy", "where-to-buy", "wheretobuy", "where-to-find", "stockist",
    "store locator", "store-locator", "storelocator", "find a store", "find-a-store",
    "buy-in-store", "points de vente", "point de vente", "où acheter", "ou-acheter",
    "revendeur", "retailer", "wo kaufen", "wo-kaufen", "händler", "haendler",
    "dónde comprar", "donde-comprar", "puntos de venta", "winkelzoeker",
]

# Big retail chains per country, with the domain of their website. A search result on one of these domains
# that mentions the brand is direct proof of distribution. Written from memory: check and edit freely.
RETAILER_DOMAINS = {
    "FR": {"carrefour.fr": "Carrefour", "auchan.fr": "Auchan", "e.leclerc": "E.Leclerc",
           "intermarche.com": "Intermarché", "monoprix.fr": "Monoprix", "franprix.fr": "Franprix",
           "sephora.fr": "Sephora", "nocibe.fr": "Nocibé"},
    "DE": {"rewe.de": "Rewe", "edeka.de": "Edeka", "dm.de": "dm", "rossmann.de": "Rossmann",
           "kaufland.de": "Kaufland"},
    "GB": {"tesco.com": "Tesco", "sainsburys.co.uk": "Sainsbury's", "asda.com": "Asda",
           "waitrose.com": "Waitrose", "ocado.com": "Ocado", "morrisons.com": "Morrisons", "boots.com": "Boots"},
    "ES": {"carrefour.es": "Carrefour", "elcorteingles.es": "El Corte Inglés", "dia.es": "Dia",
           "alcampo.es": "Alcampo"},
    "IT": {"esselunga.it": "Esselunga", "coop.it": "Coop", "conad.it": "Conad", "carrefour.it": "Carrefour"},
    "NL": {"ah.nl": "Albert Heijn", "jumbo.com": "Jumbo"},
    "SE": {"ica.se": "ICA", "coop.se": "Coop", "willys.se": "Willys"},
    "AT": {"billa.at": "Billa", "spar.at": "Spar", "hofer.at": "Hofer"},
    "BE": {"colruyt.be": "Colruyt", "delhaize.be": "Delhaize", "carrefour.be": "Carrefour"},
    "US": {"walmart.com": "Walmart", "target.com": "Target", "kroger.com": "Kroger"},
}

# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------
# What a target brand is, in one positive sentence. The extraction prompt applies it to every company it reads, so
# there is no list of excluded cases to maintain: change this sentence to aim the tool at another kind of company.
TARGET_BRAND_DEFINITION = (
    "a company that makes packaged consumer products (food, drinks, cosmetics, clothing, household goods) "
    "and sells them under its own brand name"
)
# Points for the brand's country. For now only the UK counts (the market being opened); every other country gives
# nothing, and a brand from elsewhere can still reach Tier 1 on its other points. Add "IE": 1 for example.
COUNTRY_POINTS = {"GB": 2}
TIER1_MIN_SCORE = 7   # max score is 10
TIER2_MIN_SCORE = 3

# ---------------------------------------------------------------------------------------------
# Contacts (Apollo people search)
# ---------------------------------------------------------------------------------------------
CONTACT_TIERS = ("Tier 1",)          # only the best leads get a contact
REVEAL_CONTACT_EMAIL = False         # True = ask Apollo for the email (costs credits). Not needed for a demo.

# Where the contact must be, in this order. The first place that has a suitable person wins and the next ones are
# not searched. Never anywhere else: no contact from a foreign subsidiary (Apollo has one company record per country).
CONTACT_PLACES = ["United Kingdom", "Germany", "Switzerland", "Austria", "France", "Italy", "Spain"]

# The functions that receive the retailers' integration requests, best first. These are guesses, not a validated targeting.
CONTACT_FUNCTIONS = ["supply chain", "logistics", "operations", "order management", "customer service",
                     "key account", "sales", "commercial"]
# VP, head and director are the same level for us. A manager is taken only if nobody senior is found in a function.
CONTACT_LEVELS_SENIOR = ["vp", "svp", "evp", "vice president", "head", "director"]
CONTACT_LEVELS_MANAGER = ["manager", "lead"]
# C-levels, founders and owners are never picked as the contact: they are the fallback (below).
CONTACT_TOP_LEVEL = ["chief", "ceo", "coo", "cfo", "cto", "cmo", "cso", "cro", "president", "founder", "co-founder",
                     "cofounder", "owner", "partner", "managing director", "general manager", "chairman"]
# Used only when no place has anyone in the functions above. Order = preference.
CONTACT_FALLBACK = ["founder", "owner", "ceo", "chief executive", "managing director", "general manager",
                    "president", "coo", "chief operating"]
# Functions that are never the contact (a founder or general manager is never excluded)
EXCLUDED_FUNCTIONS = {
    "finance": ["cfo", "chief financial", "finance", "financial", "accounting", "accountant", "controller",
                "treasury", "treasurer"],
    "marketing": ["marketing", "cmo", "brand", "communication", "communications", "content", "social media",
                  "growth", "pr"],
    "legal": ["legal", "counsel", "lawyer", "compliance"],
    "hr": ["hr", "human resources", "people", "talent", "recruiter", "recruiting"],
    "product/design": ["product", "design", "designer", "creative"],
    "it/tech": ["cto", "chief technology", "technology", "it", "information technology", "engineering",
                "software", "data"],
    "junior": ["intern", "assistant", "student", "apprentice", "trainee", "analyst", "coordinator", "clerk"],
}
# What we ask Apollo for, with its own seniority values, and how much of its list we read
APOLLO_SENIORITY_MAIN = ["vp", "head", "director", "manager"]
APOLLO_SENIORITY_FALLBACK = ["founder", "owner", "c_suite"]
CONTACT_PAGE_SIZE = 100    # Apollo allows up to 100 per page
CONTACT_MAX_PAGES = 3      # read the next page only if the previous one was full and held no perfect match

# ---------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------
# Cold email (written after scoring, only for the tiers listed here)
# ---------------------------------------------------------------------------------------------
EMAIL_TIERS = ("Tier 1", "Tier 2")
# The company the emails are written for. The repository only holds a neutral example: put your own name in
# `.env` (SENDER_COMPANY=...). The pitch is one sentence about what that company does; the default describes
# a B2B connectivity platform between suppliers and retailers, so only the name changes. Override it with
# PRODUCT_PITCH=... in `.env` if you want other words.
SENDER_COMPANY = _env("SENDER_COMPANY") or "Acme Connect"


def build_pitch(company: str) -> str:
    """The one-sentence description of what the company does, used in the email prompt."""
    return (
        f"{company} lets a supplier connect its ERP once and exchange orders, invoices and delivery notes "
        "with every retailer, whatever format or system each retailer uses."
    )


PRODUCT_PITCH = _env("PRODUCT_PITCH") or build_pitch(SENDER_COMPANY)
EMAIL_LANGUAGE = "English"
MAX_EMAIL_WORDS = 90
BANNED_PHRASES = [
    "i hope this", "i came across", "i wanted to", "i would love", "i'd love", "reaching out", "reach out",
    "touch base", "game-changing", "game changing", "revolutionary", "streamline", "leverage", "seamless",
    "cutting-edge", "synergy", "it is worth noting", "it's worth noting", "not just",
]

# ---------------------------------------------------------------------------------------------
# Gmail drafts (create_drafts.py)
# ---------------------------------------------------------------------------------------------
DRAFT_TIERS = ("Tier 1",)   # add "Tier 2" to also draft the manual-review leads
