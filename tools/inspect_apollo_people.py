"""Shows what Apollo really returns for one or several company domains, and which company records exist for a
brand in a given country. Prints no part of the key.

    python3 tools/inspect_apollo_people.py haribo.com haribo.de
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from leadrouter import settings  # noqa: E402

if not settings.APOLLO_API_KEY:
    raise SystemExit("No APOLLO_API_KEY found in .env")
domains = sys.argv[1:] or ["example.com"]
headers = {"x-api-key": settings.APOLLO_API_KEY, "Cache-Control": "no-cache", "Content-Type": "application/json"}
PEOPLE_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
COMPANY_URL = "https://api.apollo.io/api/v1/mixed_companies/search"


def post(url: str, body: dict):
    response = requests.post(url, headers=headers, json=body, timeout=30)
    if response.status_code >= 400:
        return None, f"HTTP {response.status_code}: {settings.redact(' '.join(response.text.split())[:200])}"
    return response.json(), ""


def people(domain: str, label: str, extra: dict) -> None:
    data, error = post(PEOPLE_URL, {"q_organization_domains_list": [domain], "per_page": 25, **extra})
    if error:
        print(f"  {label:34} -> {error}")
        return
    found = data.get("people") or []
    print(f"  {label:34} -> {len(found)} people")
    for person in found[:8]:
        org = person.get("organization") or {}
        print(f"      {str(person.get('title') or '(no title)')[:50]:50} | company: {org.get('name', '?')}")


def companies(name: str, location) -> None:
    body = {"q_organization_name": name, "per_page": 10}
    if location:
        body["organization_locations"] = [location]
    data, error = post(COMPANY_URL, body)
    label = f'name "{name}", location {location or "none"}'
    if error:
        print(f"  {label:44} -> {error}")
        return
    found = data.get("organizations") or data.get("accounts") or []
    print(f"  {label:44} -> {len(found)} companies")
    for org in found[:8]:
        place = ", ".join(str(org.get(k)) for k in ("city", "state", "country") if org.get(k)) or "(no location field)"
        print(f"      {str(org.get('name'))[:34]:34} | {str(org.get('primary_domain') or '?')[:26]:26}"
              f" | staff: {org.get('estimated_num_employees', '?')} | {place}")


for domain in domains:
    print(f"\nPeople at {domain}")
    people(domain, "no filter", {})
    people(domain, "senior roles, no filter",
           {"person_seniorities": ["founder", "owner", "c_suite", "vp", "head", "director"]})

brand = domains[0].split(".")[0]
print(f"\nCompany records named like '{brand}'")
for place in (None, "Germany", "United Kingdom"):
    companies(brand, place)
