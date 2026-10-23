import unittest

from leadrouter import scoring
from tests.helpers import make_enrichment


class ScoringTest(unittest.TestCase):
    def score(self, enrichment, country="FR", retailers=(), employees=None, site_read=True):
        return scoring.score_and_route(enrichment, country, "Brand", list(retailers), employees=employees,
                                       site_read=site_read)

    def test_the_best_case_reaches_the_maximum(self):
        result = self.score(make_enrichment(), country="GB", retailers=["A", "B", "C"], employees=500)
        self.assertEqual((result["score"], scoring.MAX_SCORE), (10, 10))
        self.assertTrue(result["routing_status"].startswith("Tier 1"))

    def test_retail_points(self):
        three = self.score(make_enrichment(), retailers=["A", "B", "C"])["score"]
        one = self.score(make_enrichment(), retailers=["A"])["score"]
        claimed = self.score(make_enrichment())["score"]
        online_only = self.score(make_enrichment(b2b_retail_distribution=False))["score"]
        self.assertEqual((three, one, claimed, online_only), (6, 5, 4, 3))

    def test_size_points(self):
        base = self.score(make_enrichment(), retailers=["A"])["score"]
        self.assertEqual(self.score(make_enrichment(), retailers=["A"], employees=300)["score"], base + 2)
        self.assertEqual(self.score(make_enrichment(), retailers=["A"], employees=80)["score"], base + 1)
        self.assertEqual(self.score(make_enrichment(), retailers=["A"], employees=10)["score"], base)
        self.assertIn("Size unknown", self.score(make_enrichment(), retailers=["A"])["score_breakdown"])

    def test_only_the_uk_gives_points_to_the_country(self):
        fr = self.score(make_enrichment(), country="FR")
        gb = self.score(make_enrichment(), country="GB")
        self.assertEqual(gb["score"], fr["score"] + 2)
        self.assertEqual(self.score(make_enrichment(), country="UK")["score"], gb["score"])   # UK is read as GB
        self.assertIn("Not a target country: FR", fr["score_breakdown"])
        self.assertIn("Country unknown", self.score(make_enrichment(), country="")["score_breakdown"])

    def test_a_brand_from_elsewhere_can_still_be_tier_one_on_strong_proof(self):
        result = self.score(make_enrichment(), country="DE", retailers=["A", "B", "C"], employees=500)
        self.assertEqual(result["score"], 8)
        self.assertTrue(result["routing_status"].startswith("Tier 1"))

    def test_a_uk_brand_sold_only_online_is_held_at_tier_two(self):
        result = self.score(make_enrichment(b2b_retail_distribution=False), country="GB", employees=500)
        self.assertEqual(result["score"], 7)                       # enough points for Tier 1...
        self.assertTrue(result["routing_status"].startswith("Tier 2"))   # ...but nothing shows it sells in shops
        self.assertIn("Held at Tier 2: no retail evidence", result["score_breakdown"])

    def test_a_site_that_could_not_be_read_is_never_tier_one(self):
        read = self.score(make_enrichment(), country="GB", retailers=["A", "B", "C"], employees=500)
        unread = self.score(make_enrichment(), country="GB", retailers=["A", "B", "C"], employees=500, site_read=False)
        self.assertTrue(read["routing_status"].startswith("Tier 1"))
        self.assertEqual(unread["score"], read["score"])           # the points are the same...
        self.assertTrue(unread["routing_status"].startswith("Tier 2"))   # ...but the tier is held back
        self.assertIn("Website unreadable: scored from web search results only", unread["score_breakdown"])
        self.assertIn("Held at Tier 2: website unreadable", unread["score_breakdown"])

    def test_an_unreadable_site_does_not_change_a_low_score(self):
        result = self.score(make_enrichment(sells_own_physical_goods=False, b2b_retail_distribution=False), site_read=False)
        self.assertTrue(result["routing_status"].startswith("Tier 3"))
        self.assertNotIn("Held at Tier 2", result["score_breakdown"])

    def test_software_is_disqualified_whatever_its_size(self):
        result = self.score(make_enrichment(sells_own_physical_goods=False, b2b_retail_distribution=False),
                            country="GB", employees=900)
        self.assertEqual(result["score"], 2)
        self.assertTrue(result["routing_status"].startswith("Tier 3"))

    def test_the_breakdown_explains_the_score(self):
        result = self.score(make_enrichment(), country="GB", retailers=["A", "B", "C"], employees=500)
        parts = [p for p in result["score_breakdown"].split(" | ") if p.startswith("+")]
        self.assertEqual(sum(int(p.split("(")[0].strip().lstrip("+")) for p in parts), result["score"])


if __name__ == "__main__":
    unittest.main()