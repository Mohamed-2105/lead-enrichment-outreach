"""Shows exactly what Apollo answers for your key, so we know why a request is refused.

    python3 tools/check_apollo_access.py

Reads APOLLO_API_KEY from .env. Prints no part of the key.
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from leadrouter import settings  # noqa: E402

if not settings.APOLLO_API_KEY:
    raise SystemExit("No APOLLO_API_KEY found in .env (is the line commented with #?)")
print(f"Key found ({len(settings.APOLLO_API_KEY)} characters)\n")

headers = {"x-api-key": settings.APOLLO_API_KEY, "Cache-Control": "no-cache", "Content-Type": "application/json"}

print("1) Company enrichment by domain (gives the headcount), domain: apollo.io")
r = requests.get("https://api.apollo.io/api/v1/organizations/enrich", headers=headers,
                 params={"domain": "apollo.io"}, timeout=20)
print("   status:", r.status_code)
print("   answer:", settings.redact(" ".join(r.text.split())[:400]), "\n")

print("2) People search (free, needs a master key)")
r = requests.post("https://api.apollo.io/api/v1/mixed_people/api_search", headers=headers,
                  json={"q_organization_domains_list": ["apollo.io"], "per_page": 1}, timeout=20)
print("   status:", r.status_code)
print("   answer:", settings.redact(" ".join(r.text.split())[:400]))
