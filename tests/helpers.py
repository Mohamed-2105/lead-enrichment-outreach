"""Shared test helpers. No test ever needs a real key or a real network call."""

import unittest
from unittest import mock

from leadrouter import contacts, retail
from leadrouter.llm import LeadEnrichment


def response(status: int = 200, text: str = "", **data):
    """A fake HTTP response."""
    fake = mock.Mock()
    fake.status_code = status
    fake.text = text
    fake.raise_for_status = (lambda: None) if status < 400 else mock.Mock(side_effect=Exception(f"HTTP {status}"))
    fake.json.return_value = data
    return fake


def make_enrichment(**overrides) -> LeadEnrichment:
    values = dict(industry="FMCG / Food & Beverage", sells_own_physical_goods=True, b2b_retail_distribution=True,
                  retailers_named=[], employees_estimate=0, country_code="", company_summary="Biscuits.")
    values.update(overrides)
    return LeadEnrichment(**values)


def reset_state() -> None:
    retail.APOLLO_STATS.clear()
    retail.APOLLO_BLOCKED["reason"] = ""
    contacts.APOLLO_PEOPLE_BLOCKED["reason"] = ""


class Base(unittest.TestCase):
    """Starts every test with a clean Apollo state."""

    def setUp(self):
        reset_state()
        self.addCleanup(reset_state)
