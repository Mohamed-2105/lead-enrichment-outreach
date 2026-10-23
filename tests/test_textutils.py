import unittest

from leadrouter import textutils as t


class TextUtilsTest(unittest.TestCase):
    def test_connectors_and_accents_are_ignored_when_comparing_names(self):
        self.assertEqual(t.compact("Alba & Bruno"), t.compact("Alba et Bruno"))
        self.assertEqual(t.compact("Alba and Bruno"), "albabruno")
        self.assertEqual(t.compact("Rob & Sally's"), "robsallys")
        self.assertEqual(t.normalize_text("  Nocibé   PARIS "), "nocibe paris")

    def test_core_name_drops_generic_words(self):
        self.assertEqual(t.core_name("Kelmo Foods"), "kelmo")
        self.assertEqual(t.core_name("Alba et Bruno"), "albabruno")
        self.assertEqual(t.core_name("Foods"), "foods")   # never empty

    def test_clean_country(self):
        for given, expected in [("FR", "FR"), ("fr", "FR"), ("UK", "GB"), ("France", "FR"), ("Allemagne", "DE"),
                                ("EU", ""), ("", ""), (None, ""), (float("nan"), ""), ("Narnia", "")]:
            self.assertEqual(t.clean_country(given), expected, given)

    def test_country_from_the_website_extension(self):
        self.assertEqual(t.tld_country("https://www.zanto.de/x"), "DE")
        self.assertEqual(t.tld_country("https://tesco.co.uk"), "GB")
        self.assertEqual(t.tld_country("https://www.plumo.com"), "")

    def test_headcounts(self):
        for given, expected in [(120, 120), ("1,200", 1200), ("51-200", 51), ("", None), (None, None),
                                (float("nan"), None), ("abc", None)]:
            self.assertEqual(t.parse_employees(given), expected, given)

    def test_whole_word_matching(self):
        self.assertTrue(t.has_word("VP, Supply Chain", "vp"))
        self.assertFalse(t.has_word("Marketing Coordinator", "coo"))
        self.assertFalse(t.has_word("Head of Procurement", "pr"))

    def test_search_link_never_contains_nan(self):
        link = t.build_search_link("Plumo", float("nan"))
        self.assertNotIn("nan", link.lower())
        self.assertTrue(link.startswith("https://www.google.com/search?q=Plumo"))

    def test_domain(self):
        self.assertEqual(t.normalize_domain("https://www.Plumo.com/en"), "plumo.com")
        self.assertEqual(t.normalize_domain("plumo.com"), "plumo.com")


if __name__ == "__main__":
    unittest.main()