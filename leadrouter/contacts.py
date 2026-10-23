"""Finding who to write to, with Apollo's people search. Plain rules, no model.

The rule, in four lines:
  1. Look in the places of CONTACT_PLACES, in order (the UK first). The first place with a suitable person wins.
  2. A suitable person works in one of CONTACT_FUNCTIONS (supply chain first) at VP, head or director level
     (a manager if nobody senior), never in finance, marketing... and never a C-level, a founder or an owner.
  3. Among suitable people: the best function first, then the highest level.
  4. If no place has anyone, fall back on founders and general managers, looking in the same order of places.
"""

import re
import time

import requests

from . import settings
from .log import log
from .textutils import has_word, name_words, normalize_domain, CONNECTOR_WORDS, DESCRIPTOR_WORDS

PEOPLE_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
COMPANY_URL = "https://api.apollo.io/api/v1/mixed_companies/search"
MATCH_URL = "https://api.apollo.io/api/v1/people/match"

APOLLO_PEOPLE_BLOCKED = {"reason": ""}   # filled after a 401/403: we stop calling Apollo and say why


class ApolloAccessError(Exception):
    """Apollo refused the key or the plan."""


# ---------------------------------------------------------------------------------------------
# The rules (no network)
# ---------------------------------------------------------------------------------------------
def without_vice_president(title: str) -> str:
    """'Vice President' must not count as 'President' (a C-level)."""
    return re.sub(r"\b(?:senior |executive |group )?vice[ -]president\b", " ", title.lower())


def excluded_reason(title: str) -> str:
    """The function that rules this person out (finance, marketing, junior...), or '' if none.
    A founder or general manager is never excluded."""
    plain = without_vice_president(title)
    if any(has_word(plain, w) for w in settings.CONTACT_FALLBACK):
        return ""
    for function, words in settings.EXCLUDED_FUNCTIONS.items():
        if any(has_word(title, w) for w in words):
            return function
    return ""


def is_top_level(title: str) -> bool:
    """C-levels, founders and owners: they do not answer a cold email about order formats."""
    plain = without_vice_president(title)
    return any(has_word(plain, w) for w in settings.CONTACT_TOP_LEVEL)


def function_rank(title: str) -> int | None:
    """Position of the best function found in the title (0 = best), or None."""
    ranks = [i for i, word in enumerate(settings.CONTACT_FUNCTIONS) if has_word(title, word)]
    return min(ranks) if ranks else None


def level_tier(title: str) -> int:
    """0 = VP, head or director (interchangeable), 1 = manager, 2 = no level word (for example a title in another
    language: Apollo has already filtered on seniority, so the person is at least a manager)."""
    if any(has_word(title, w) for w in settings.CONTACT_LEVELS_SENIOR):
        return 0
    if any(has_word(title, w) for w in settings.CONTACT_LEVELS_MANAGER):
        return 1
    return 2


def contact_key(title: str) -> tuple | None:
    """Sort key of a title (smaller = better), or None if this person is not a suitable contact."""
    if excluded_reason(title) or is_top_level(title):
        return None
    rank = function_rank(title)
    return None if rank is None else (rank, level_tier(title))


def pick_contact(people: list[dict]) -> dict | None:
    """The best suitable person: best function first, then the highest level. On a tie, Apollo's order."""
    best, best_key = None, None
    for person in people:
        key = contact_key(str(person.get("title") or ""))
        if key is not None and (best_key is None or key < best_key):
            best, best_key = person, key
    return best


def pick_fallback(people: list[dict]) -> dict | None:
    """Founders and general managers, in the order of CONTACT_FALLBACK."""
    best, best_rank = None, None
    for person in people:
        title = str(person.get("title") or "")
        if excluded_reason(title):
            continue
        plain = without_vice_president(title)
        ranks = [i for i, word in enumerate(settings.CONTACT_FALLBACK) if has_word(plain, word)]
        if ranks and (best_rank is None or min(ranks) < best_rank):
            best, best_rank = person, min(ranks)
    return best


def why_not(title: str) -> str:
    """Why a person from Apollo's list was not chosen, for the candidates column."""
    reason = excluded_reason(title)
    if reason:
        return f"excluded: {reason}"
    if is_top_level(title):
        return "C-level or founder (fallback only)"
    if function_rank(title) is None:
        return "no matching function"
    return "not chosen (a better function or level was found)"


def label_candidates(people: list[dict], picked: dict | None) -> str:
    """What happened to each person Apollo returned, so the choice can be checked by eye."""
    out = []
    for person in people[:8]:
        title = str(person.get("title") or "(no title)")
        decision = "PICKED" if (picked is not None and person is picked) else why_not(title)
        out.append(f"{title} @ {person.get('_place', '?')} [{decision}]")
    return " | ".join(out[:6])


def org_matches_brand(org_name: str, company_name: str) -> bool:
    """True if every main word of the brand is a whole word of the company record's name.
    'Haribo Turkey' and 'HARIBO of America, Inc.' match 'Haribo'; 'Haribon Foundation' does not."""
    brand = [w for w in name_words(company_name) if w != "&" and w not in CONNECTOR_WORDS]
    brand = [w for w in brand if w not in DESCRIPTOR_WORDS] or brand
    tokens = set(name_words(org_name))
    return bool(brand) and all(w in tokens for w in brand)


# ---------------------------------------------------------------------------------------------
# Talking to Apollo
# ---------------------------------------------------------------------------------------------
def empty_contact() -> dict:
    return {"name": "", "first_name": "", "title": "", "email": "", "status": "", "candidates": "",
            "location": "", "company": ""}


def _headers() -> dict:
    return {"x-api-key": settings.APOLLO_API_KEY, "Cache-Control": "no-cache", "Content-Type": "application/json"}


def _post(url: str, body: dict, attempts: int = 3):
    """One POST, repeated with a growing pause on a rate limit (429) or a server error (5xx)."""
    response = None
    for attempt in range(attempts):
        response = requests.post(url, headers=_headers(), json=body, timeout=20)
        if response.status_code != 429 and response.status_code < 500:
            break
        time.sleep(settings.RETRY_BASE_DELAY * (2 ** attempt))
    return response


def _search_people(where: dict, extra: dict) -> list[dict]:
    """People search (free, no credits). `where` says which company records or which domain and place.
    Only a 401 or 403 means Apollo refuses the key or plan and stops every later lookup; any other error only
    costs this lead."""
    response = _post(PEOPLE_URL, {"per_page": settings.CONTACT_PAGE_SIZE, **where, **extra})
    if response.status_code in (401, 403):
        text = " ".join(response.text.split())[:250]
        APOLLO_PEOPLE_BLOCKED["reason"] = f"{response.status_code} {text}"
        log(f"    !! APOLLO PEOPLE SEARCH REFUSED ({response.status_code}): {text}")
        raise ApolloAccessError(f"error {response.status_code}: {text}")
    if response.status_code >= 400:
        raise RuntimeError(f"Apollo answered {response.status_code}: {' '.join(response.text.split())[:150]}")
    return response.json().get("people") or []


def domain_root(domain: str) -> str:
    """The brand part of a domain, letters and digits only: 'www.tyrrellscrisps.co.uk' -> 'tyrrellscrisps'."""
    labels = normalize_domain(domain).split(".")
    if len(labels) >= 3 and labels[-2] in {"co", "com", "org", "net", "ac", "gov"}:
        root = labels[-3]
    else:
        root = labels[-2] if len(labels) >= 2 else labels[0]
    return re.sub(r"[^a-z0-9]", "", root)


def _company_records(company_name: str, place: str, domain: str = "") -> list[tuple[str, str]]:
    """(id, name) of the brand's company records headquartered in this place.
    A brand has one record per subsidiary (Haribo Turkey, Haribo France...), so the domain alone is not enough.
    But several unrelated companies can share the name (a crisps maker and a golf club called Tyrrells). So when
    Apollo gives the records' domains, a record is kept only if its domain has the same root as the brand's website,
    and if none does, none is kept (the search then goes by domain). Only when Apollo gives no domain at all is the
    biggest record kept."""
    try:
        response = _post(COMPANY_URL, {"q_organization_name": company_name, "organization_locations": [place],
                                       "per_page": 25})
        if response.status_code >= 400:
            log(f"    ! Apollo company search failed ({response.status_code}): {' '.join(response.text.split())[:120]}")
            return []
        data = response.json()
    except Exception as e:
        log(f"    ! Apollo company search failed: {e}")
        return []
    records = [(o.get("id"), o) for o in (data.get("organizations") or [])]
    records += [(a.get("organization_id") or a.get("id"), a) for a in (data.get("accounts") or [])]
    found = [(oid, o) for oid, o in records if oid and org_matches_brand(str(o.get("name") or ""), company_name)]
    found.sort(key=lambda item: -(item[1].get("estimated_num_employees") or 0))
    root = domain_root(domain) if domain else ""
    with_domain = [(oid, o) for oid, o in found if o.get("primary_domain")]
    if root and with_domain:
        chosen = [(oid, o) for oid, o in with_domain if domain_root(str(o["primary_domain"])) == root][:3]
    else:
        chosen = found[:1]
    return [(oid, f"{o.get('name')} ({o['primary_domain']})" if o.get("primary_domain") else str(o.get("name")))
            for oid, o in chosen]


def _wheres(domain: str, company_name: str, place: str) -> list[tuple[dict, str]]:
    """The filters to try for this place, best first, each with a short description: the brand's own company records
    located there, then the brand's domain restricted to people living there. The second one is tried even when
    the first found a record, because a record can exist without any person attached to it."""
    attempts = []
    records = _company_records(company_name, place, domain) if company_name else []
    if records:
        attempts.append(({"organization_ids": [oid for oid, _ in records]},
                         f"company {' / '.join(name for _, name in records)}"))
    attempts.append(({"q_organization_domains_list": [domain], "person_locations": [place]}, f"domain {domain}"))
    return attempts


def _best_in_place(where: dict, place: str) -> tuple[dict | None, list[dict]]:
    """Reads up to CONTACT_MAX_PAGES pages of suitable-level people and returns the best contact and everyone seen.
    Stops early on a page that is not full, or as soon as a perfect match (best function, senior level) appears."""
    seen: list[dict] = []
    for page in range(1, settings.CONTACT_MAX_PAGES + 1):
        people = _search_people(where, {
            "person_titles": settings.CONTACT_FUNCTIONS,
            "person_seniorities": settings.APOLLO_SENIORITY_MAIN,
            "page": page,
        })
        seen += [{**p, "_place": place} for p in people]
        best = pick_contact(seen)
        perfect = best is not None and contact_key(str(best.get("title") or "")) == (0, 0)
        if perfect or len(people) < settings.CONTACT_PAGE_SIZE:
            break
    return pick_contact(seen), seen


def _reveal_email(person: dict, domain: str, first: str, contact: dict) -> None:
    """Optional and off by default: asking Apollo for the email costs credits."""
    response = _post(MATCH_URL, {"id": person["id"], "domain": domain})
    response.raise_for_status()
    matched = response.json().get("person") or {}
    contact["email"] = str(matched.get("email") or "")
    if matched.get("last_name"):
        contact["name"] = f"{first} {matched['last_name']}".strip()
    contact["status"] = "ok (email revealed)" if contact["email"] else "ok (no email found)"


# ---------------------------------------------------------------------------------------------
# The whole search
# ---------------------------------------------------------------------------------------------
def find_contact(domain: str, company_name: str = "") -> dict:
    """Finds the right person at the company. Always returns a dict whose status explains the result."""
    contact = empty_contact()
    if not settings.APOLLO_API_KEY:
        contact["status"] = "not_configured (no APOLLO_API_KEY)"
        return contact
    if not domain:
        contact["status"] = "no_domain"
        return contact
    if APOLLO_PEOPLE_BLOCKED["reason"]:
        contact["status"] = f"skipped after access error: {APOLLO_PEOPLE_BLOCKED['reason']}"
        return contact
    if not settings.CONTACT_PLACES:
        contact["status"] = "no_place (CONTACT_PLACES is empty)"
        return contact

    try:
        wheres: dict[str, list[tuple[dict, str]]] = {}
        everyone: list[dict] = []
        notes: list[str] = []
        person, place_found, how, fallback = None, "", "", False

        for place in settings.CONTACT_PLACES:                      # 1. the functions, place by place
            wheres[place] = _wheres(domain, company_name, place)
            for where, label in wheres[place]:
                best, seen = _best_in_place(where, place)
                everyone += seen
                notes.append(f"{place} via {label}: {len(seen)} people read")
                if best:
                    person, place_found, how = best, place, label
                    break
            if person:
                break

        if person is None:                                         # 2. the fallback, same order of places
            for place in settings.CONTACT_PLACES:
                for where, label in wheres[place]:
                    people = [{**p, "_place": place} for p in _search_people(
                        where, {"person_seniorities": settings.APOLLO_SENIORITY_FALLBACK})]
                    everyone += people
                    best = pick_fallback(people)
                    if best:
                        person, place_found, how, fallback = best, place, label, True
                        break
                if person:
                    break

        contact["candidates"] = label_candidates(everyone, person)
        if person is None:
            seen_titles = [str(p.get("title") or "") for p in everyone if p.get("title")][:5]
            contact["status"] = (f"no_match (searched {'; '.join(notes)}"
                                 + (f"; titles seen: {' | '.join(seen_titles)}" if seen_titles else "; nobody found") + ")")
            return contact

        first = str(person.get("first_name") or "").strip()
        last = str(person.get("last_name") or "").strip()   # Apollo hides last names on a free search
        kind = "fallback: founder or general manager" if fallback else "function match"
        contact.update({
            "first_name": first, "name": f"{first} {last}".strip(), "title": str(person.get("title") or ""),
            "location": place_found, "company": how,
            "status": f"ok ({kind}; searched in {place_found} via {how})",
        })
        if settings.REVEAL_CONTACT_EMAIL and person.get("id"):
            _reveal_email(person, domain, first, contact)
        return contact
    except ApolloAccessError as e:
        contact["status"] = str(e)
        return contact
    except Exception as e:
        contact["status"] = f"error: {settings.redact(e)[:200]}"
        log(f"    ! Apollo contact lookup failed: {e}")
        return contact