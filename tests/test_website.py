import unittest
from unittest import mock

from leadrouter import website as w


class SlugTest(unittest.TestCase):
    def test_every_spelling_of_the_connector_is_a_valid_domain(self):
        for name in ("Alba et Bruno", "Alba & Bruno", "Alba and Bruno"):
            variants = w.slug_variants(name)
            self.assertIn("albaetbruno", variants, name)
            self.assertIn("albabruno", variants, name)

    def test_descriptor_can_be_dropped(self):
        self.assertIn("kelmo", w.slug_variants("Kelmo Foods"))
        self.assertIn("kelmofoods", w.slug_variants("Kelmo Foods"))

    def test_host_slug(self):
        self.assertEqual(w.host_slug("www.plumodrinks.co.uk"), "plumodrinks")
        self.assertEqual(w.host_slug("shop.brand-x.com"), "brandx")

    def test_social_networks_and_databases_are_not_company_sites(self):
        for host in ("fr.wikipedia.org", "www.linkedin.com", "x.com", "www.amazon.fr", "www.crunchbase.com"):
            self.assertFalse(w.is_company_site(host), host)
        for host in ("www.streamly.com", "www.albaetbruno.com"):
            self.assertTrue(w.is_company_site(host), host)


class FindWebsiteTest(unittest.TestCase):
    def run_find(self, name, links, country="", pages=None, verdicts=None):
        pages = pages or {}
        verdicts = verdicts or {}
        results = [{"title": "t", "url": link, "content": ""} for link in links]

        def identity(url):
            if url in pages:
                return pages[url]
            raise Exception("unreadable")

        def check(company, ctry, url, text):
            return verdicts.get(url, False), ("matches" if verdicts.get(url) else "page is about something else")

        with mock.patch.object(w, "web_search", return_value=results), \
             mock.patch.object(w, "page_identity_text", side_effect=identity), \
             mock.patch.object(w, "check_website_identity", side_effect=check):
            return w.find_website(name, country)

    def test_exact_domain_confirmed_by_the_model(self):
        url, confidence, _ = self.run_find(
            "Alba et Bruno", ["https://fr.wikipedia.org/wiki/x", "https://www.albaetbruno.com/fr"],
            pages={"https://www.albaetbruno.com": "Title: Alba et Bruno"},
            verdicts={"https://www.albaetbruno.com": True})
        self.assertEqual((url, confidence), ("https://www.albaetbruno.com", "high"))

    def test_streamly_foods_is_rejected_and_every_candidate_is_explained(self):
        url, confidence, note = self.run_find(
            "Streamly Foods", ["https://www.streamly.com/browse", "https://www.streamly.shop/"],
            pages={"https://www.streamly.com": "Title: Streamly - Watch TV Shows Online"})
        self.assertEqual(confidence, "low")
        self.assertIn("streamly.com", note)
        self.assertIn("streamly.shop could not be read", note)

    def test_name_not_on_the_page_is_rejected_without_asking_the_model(self):
        _, confidence, note = self.run_find(
            "Zorblax", ["https://www.randomshop.com/"], pages={"https://www.randomshop.com": "Title: Random Shop"})
        self.assertEqual(confidence, "low")
        self.assertIn("company name not found", note)

    def test_without_a_country_the_dotcom_comes_before_a_regional_site(self):
        pages = {u: "Title: Alba et Bruno" for u in ("https://www.albaetbruno.us", "https://www.albaetbruno.com")}
        verdicts = {u: True for u in pages}
        links = ["https://www.albaetbruno.us/", "https://www.albaetbruno.com/fr"]
        self.assertEqual(self.run_find("Alba et Bruno", links, "", pages, verdicts)[0], "https://www.albaetbruno.com")
        self.assertEqual(self.run_find("Alba et Bruno", links, "FR", pages, verdicts)[0], "https://www.albaetbruno.us")

    def test_a_blocked_page_is_accepted_when_the_domain_spells_the_full_name(self):
        url, confidence, note = self.run_find("Cawston Press", ["https://cawstonpress.com/shop"])   # nothing can be read
        self.assertEqual((url, confidence), ("https://cawstonpress.com", "medium"))
        self.assertIn("could not be read", note)

    def test_a_blocked_page_is_not_accepted_on_a_partial_match(self):
        # streamly.com spells "Streamly", not "Streamly Foods": the descriptor was dropped, so nothing proves it
        url, confidence, _ = self.run_find("Streamly Foods", ["https://www.streamly.com/browse"])
        self.assertEqual(confidence, "low")

    def test_a_blocked_page_on_an_unrelated_domain_stays_unconfirmed(self):
        self.assertEqual(self.run_find("Cawston Press", ["https://www.drinksupermarket.com/"])[1], "low")

    def test_nothing_usable(self):
        self.assertEqual(self.run_find("Nothing", ["https://www.amazon.fr/dp/1"]), ("", "", "no usable search result"))


if __name__ == "__main__":
    unittest.main()