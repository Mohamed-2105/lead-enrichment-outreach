"""Everything that talks to Gemini: the structured answers we expect, the prompts, the retries and the fallback."""

import time
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from . import settings
from .log import log

_client = None


def get_client():
    """Creates the Gemini client on first use, so importing the code never needs a key."""
    global _client
    if _client is None:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing. Add it to your .env file (see .env.example).")
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


class LeadEnrichment(BaseModel):
    industry: Literal[
        "FMCG / Food & Beverage",
        "Retail / Fashion / Cosmetics",
        "Manufacturing / Consumer Goods",
        "Logistics / Supply Chain SaaS or Service",
        "B2B SaaS / Fintech / Other",
    ] = Field(description="Main category of the company.")

    sells_own_physical_goods: bool = Field(
        description=f"True ONLY if the company is {settings.TARGET_BRAND_DEFINITION}. False otherwise."
    )

    b2b_retail_distribution: bool = Field(
        description=(
            "True only if the content shows that products are sold through retailers, "
            "supermarkets, pharmacies or wholesale. False if the content only shows "
            "online sales, or says nothing about it."
        )
    )

    retailers_named: list[str] = Field(
        description=(
            "Names of retailers, chains or stores that the evidence explicitly names as selling "
            "THIS company's products. Copy each name exactly as written. "
            "Empty list if none is named. Never use outside knowledge."
        )
    )

    employees_estimate: int = Field(
        description=(
            "Number of employees if the evidence states one (for a range, use the lower bound). "
            "0 if no number is stated. Never guess."
        )
    )

    country_code: str = Field(
        description=(
            "Two-letter ISO code (FR, DE, GB, ES, IT, NL, SE, AT, BE, US...) of the country where the company "
            "is based, only if the evidence shows it (address, legal notice, language and currency). "
            "Empty string if unknown."
        )
    )

    company_summary: str = Field(description="One short sentence on what the company sells (in English).")


class EmailDraft(BaseModel):
    subject: str = Field(description="Subject line, under 8 words, mentioning the brand name only (no retailer).")
    body: str = Field(description="Plain-text email body with real line breaks: the greeting on its own line, a blank line between paragraphs, the sign-off on its own lines.")

class WebsiteCheck(BaseModel):
    is_official_site: bool = Field(
        description="True only if the page is the official website of the company with this exact name."
    )
    reason: str = Field(description="One short sentence explaining the answer.")


def build_prompt(company_name: str, url: str, scraped_text: str) -> str:
    return f"""
You are a GTM Engineer. Analyze this company to decide whether it is a target brand, that is to say
{settings.TARGET_BRAND_DEFINITION}.

RULES:
- "sells_own_physical_goods" is true only if the company is a target brand as defined above, and false otherwise.
- "b2b_retail_distribution" is true only if the evidence shows that its products are sold through other companies' shops or wholesale.
- Base every answer ONLY on the evidence below (the website and, if present, web search results). If the evidence is not enough to decide, answer false.
- "retailers_named": only names written in the evidence, and only retailers that stock THIS company's products. Never add a retailer from your own knowledge.
- "employees_estimate": only a number stated in the evidence about THIS company, otherwise 0.

Examples:
- A biscuit brand whose site lists supermarkets that stock it -> sells_own_physical_goods: true, b2b_retail_distribution: true, retailers_named: those supermarkets
- A brand whose site only has an online shop -> sells_own_physical_goods: true, b2b_retail_distribution: false, retailers_named: []
- A transport-tracking software company -> sells_own_physical_goods: false, b2b_retail_distribution: false

Company: {company_name}
URL: {url}
Evidence:
{scraped_text}
""".strip()


def call_model(model_name: str, prompt: str) -> LeadEnrichment:
    response = get_client().models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=LeadEnrichment,
            temperature=0.0,
        ),
    )
    if response.parsed is not None:
        return response.parsed
    return LeadEnrichment.model_validate_json(response.text)


def analyze_lead(company_name: str, url: str, scraped_text: str) -> tuple[LeadEnrichment, str]:
    prompt = build_prompt(company_name, url, scraped_text)
    last_error = None

    for model_name in (settings.PRIMARY_MODEL, settings.FALLBACK_MODEL):
        for attempt in range(1, settings.MAX_RETRIES_PER_MODEL + 1):
            try:
                return call_model(model_name, prompt), model_name
            except Exception as e:
                last_error = e
                log(f"    ! {model_name} attempt {attempt}/{settings.MAX_RETRIES_PER_MODEL} failed: {e}")
                if attempt < settings.MAX_RETRIES_PER_MODEL:
                    time.sleep(settings.RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        log(f"    -> switching away from {model_name}")

    raise RuntimeError(f"Both models failed. Last error: {last_error}")


def generate_structured(prompt: str, schema, temperature: float = 0.3):
    """One structured call, primary model then fallback. Returns the parsed object."""
    last_error = None
    for model_name in (settings.PRIMARY_MODEL, settings.FALLBACK_MODEL):
        for attempt in range(settings.MAX_RETRIES_PER_MODEL):
            try:
                response = get_client().models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=temperature,
                    ),
                )
                return response.parsed or schema.model_validate_json(response.text)
            except Exception as e:
                last_error = e
                time.sleep(settings.RETRY_BASE_DELAY * (2 ** attempt))
    raise RuntimeError(f"Both models failed. Last error: {last_error}")


def check_website_identity(company_name: str, country: str, url: str, identity_text: str) -> tuple[bool | None, str]:
    """Asks the model whether the page belongs to this company. (None, reason) if the model fails."""
    prompt = f"""
You verify company websites.

Company name: {company_name} (country: {country})
Candidate website: {url}
Page content:
{identity_text}

Is this the official website of the company with this exact name?
Rules:
- The name may include a generic word such as Foods, Drinks, Group or Labs. The site must belong to a company
  that matches the whole name, not to a larger or different company that only shares part of it.
- If the page describes a very different business from what the name suggests, answer false.
- If the content is not enough to decide, answer false.
""".strip()
    last_error = ""
    for model_name in (settings.PRIMARY_MODEL, settings.FALLBACK_MODEL):
        for attempt in range(settings.MAX_RETRIES_PER_MODEL):
            try:
                response = get_client().models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=WebsiteCheck,
                        temperature=0.0,
                    ),
                )
                check = response.parsed or WebsiteCheck.model_validate_json(response.text)
                return check.is_official_site, check.reason
            except Exception as e:
                last_error = str(e)[:150]
                time.sleep(settings.RETRY_BASE_DELAY * (2 ** attempt))
    return None, f"model check failed: {last_error}"
