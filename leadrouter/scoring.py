"""The score: plain rules, no model, so a lead's rank can always be explained from `score_breakdown`.

  +3 the company sells its own physical goods
  +3 / +2 / +1  three or more retailers verified / one or two / retail claimed but not verified
  +2 the brand is from a country in COUNTRY_POINTS (the UK, for now)
  +2 / +1  200 or more employees / 50 to 199
Maximum 10. Tier 1 from TIER1_MIN_SCORE, and only with some retail evidence and a website that could be read.
Tier 2 from TIER2_MIN_SCORE.
"""

from . import settings
from .llm import LeadEnrichment

MAX_SCORE = 10


def score_and_route(enrichment: LeadEnrichment, country: str, company_name: str,
                    verified_retailers: list[str], employees: int | None = None,
                    site_read: bool = True) -> dict:
    score = 0
    reasons = []

    if enrichment.sells_own_physical_goods:
        score += 3
        reasons.append("+3 (Own Physical Goods)")

        n = len(verified_retailers)
        if n >= 3:
            score += 3
            reasons.append(f"+3 ({n} retailers verified)")
        elif n >= 1:
            score += 2
            reasons.append(f"+2 ({n} retailer verified)")
        elif enrichment.b2b_retail_distribution:
            score += 1
            reasons.append("+1 (Retail claimed, not verified)")
        else:
            reasons.append("+0 (No retail evidence)")
    else:
        reasons.append("+0 (No Physical Goods)")

    code = str(country).strip().upper()
    code = "GB" if code == "UK" else code
    if not code:
        reasons.append("+0 (Country unknown)")
    elif settings.COUNTRY_POINTS.get(code, 0):
        points = settings.COUNTRY_POINTS[code]
        score += points
        reasons.append(f"+{points} (Target country: {code})")
    else:
        reasons.append(f"+0 (Not a target country: {code})")

    if enrichment.sells_own_physical_goods:
        if employees is None:
            reasons.append("+0 (Size unknown)")
        elif employees >= 200:
            score += 2
            reasons.append(f"+2 (Size: {employees} employees)")
        elif employees >= 50:
            score += 1
            reasons.append(f"+1 (Size: {employees} employees)")
        else:
            reasons.append(f"+0 (Small: {employees} employees)")

    if not site_read:
        reasons.append("Website unreadable: scored from web search results only")

    # Tier 1 gets a contact and an email, so it needs a sign that the brand sells through retailers and a website
    # that was actually read: an analysis from search snippets alone is never enough to send anyone a message.
    has_retail_signal = bool(verified_retailers) or enrichment.b2b_retail_distribution
    if score >= settings.TIER1_MIN_SCORE and has_retail_signal and site_read:
        tier = "Tier 1 - Hot Lead (Sequence A)"
    elif score >= settings.TIER2_MIN_SCORE:
        tier = "Tier 2 - Manual Review (Sequence B)"
        if score >= settings.TIER1_MIN_SCORE:
            reasons.append("Held at Tier 2: " + ("no retail evidence" if not has_retail_signal else "website unreadable"))
    else:
        tier = "Tier 3 - Disqualified"

    return {
        "score": score,
        "score_breakdown": " | ".join(reasons),
        "routing_status": tier,
    }