import unittest
from unittest import mock

from leadrouter import contacts as c
from leadrouter import settings
from tests.helpers import Base, response


class TitleRulesTest(unittest.TestCase):
    def test_function_then_level(self):
        for title, key in [("VP, Supply Chain", (0, 0)), ("Head of Logistics", (1, 0)), ("Director of Operations", (2, 0)),
                           ("Vice President of Operations", (2, 0)), ("Supply Chain Manager", (0, 1)),
                           ("Sales Director", (6, 0)), ("Head of Order Management", (3, 0))]:
            self.assertEqual(c.contact_key(title), key, title)

    def test_vp_head_and_director_are_the_same_level(self):
        keys = {c.contact_key(f"{level} Supply Chain") for level in ("VP", "Head of", "Director")}
        self.assertEqual(keys, {(0, 0)})

    def test_c_levels_founders_and_owners_are_never_a_contact(self):
        for title in ("Chief Supply Chain Officer", "Chief Operating Officer", "CEO", "Co-Founder & CEO", "Owner",
                      "President", "Managing Director", "General Manager"):
            self.assertIsNone(c.contact_key(title), title)
        self.assertEqual(c.contact_key("Vice President, Supply Chain"), (0, 0))   # a vice president is not a president

    def test_functions_that_never_buy_are_excluded(self):
        for title, why in [("Chief Financial Officer", "finance"), ("VP Finance", "finance"), ("VP Marketing", "marketing"),
                           ("Marketing Operations Director", "marketing"), ("IT Operations Director", "it/tech"),
                           ("Head of People", "hr"), ("Supply Chain Analyst", "junior"), ("Operations Coordinator", "junior")]:
            self.assertEqual(c.excluded_reason(title), why, title)
            self.assertIsNone(c.contact_key(title), title)

    def test_the_function_comes_before_the_level(self):
        people = [{"first_name": "Tom", "title": "VP Operations"}, {"first_name": "Mia", "title": "Supply Chain Manager"}]
        self.assertEqual(c.pick_contact(people)["first_name"], "Mia")     # supply chain beats operations, even as a manager

    def test_supply_chain_beats_operations_at_the_same_level(self):
        people = [{"first_name": "Tom", "title": "Director of Operations"}, {"first_name": "Eva", "title": "Head of Supply Chain"}]
        self.assertEqual(c.pick_contact(people)["first_name"], "Eva")

    def test_a_manager_is_taken_only_if_nobody_is_senior_in_that_function(self):
        people = [{"first_name": "Mia", "title": "Supply Chain Manager"}, {"first_name": "Eva", "title": "Supply Chain Director"}]
        self.assertEqual(c.pick_contact(people)["first_name"], "Eva")
        self.assertEqual(c.pick_contact(people[:1])["first_name"], "Mia")

    def test_nobody_suitable(self):
        self.assertIsNone(c.pick_contact([{"title": "Chief Financial Officer"}, {"title": "Marketing Manager"}, {}]))

    def test_fallback_is_founders_and_general_managers_in_order(self):
        people = [{"first_name": "Ida", "title": "Chief Executive Officer"}, {"first_name": "Luc", "title": "Co-Founder"}]
        self.assertEqual(c.pick_fallback(people)["first_name"], "Luc")
        self.assertIsNone(c.pick_fallback([{"title": "Chief Financial Officer"}, {"title": "Chief Marketing Officer"}]))
        self.assertIsNone(c.pick_fallback([{"title": "Vice President, Business Development"}]))
        self.assertEqual(c.pick_fallback([{"first_name": "Max", "title": "Co-Founder & CFO"}])["first_name"], "Max")

    def test_company_records_carry_the_brand_as_a_whole_word(self):
        for name in ("Zanto Turkey", "ZANTO of America, Inc.", "ZANTO International", "Zanto"):
            self.assertTrue(c.org_matches_brand(name, "Zanto"), name)
        for name in ("Zantoni Foundation", "ZantoL"):
            self.assertFalse(c.org_matches_brand(name, "Zanto"), name)
        self.assertTrue(c.org_matches_brand("Alba & Bruno UK", "Alba et Bruno"))

    def test_the_places_are_in_the_order_asked(self):
        self.assertEqual(settings.CONTACT_PLACES,
                         ["United Kingdom", "Germany", "Switzerland", "Austria", "France", "Italy", "Spain"])


class FindContactTest(Base):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(settings, "APOLLO_API_KEY", "key-for-tests-123"),
                        mock.patch("leadrouter.contacts.time.sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []

    def router(self, companies=None, main=None, fallback=None):
        """Fake Apollo. companies: place -> records. main / fallback: ('domain', place) or ('orgs', id...) -> people."""
        def post(url, **kw):
            body = kw["json"]
            if "mixed_companies" in url:
                place = body["organization_locations"][0]
                self.calls.append(("companies", place))
                return response(organizations=(companies or {}).get(place, []))
            key = ("orgs", *body["organization_ids"]) if "organization_ids" in body else ("domain", body["person_locations"][0])
            kind = "main" if "person_titles" in body else "fallback"
            self.calls.append(("people", kind, key, body.get("page", 1)))
            return response(people=((main if kind == "main" else fallback) or {}).get(key, []))
        return post

    def find(self, **tables):
        with mock.patch("requests.post", side_effect=self.router(**tables)) as post:
            contact = c.find_contact("brand.com", "Brand")
        self.post = post
        return contact

    def searched_places(self):
        return [call[1] for call in self.calls if call[0] == "companies"]

    def test_the_uk_wins_and_nothing_else_is_searched(self):
        contact = self.find(main={("domain", "United Kingdom"): [{"first_name": "Jo", "title": "Logistics Manager"}],
                                  ("domain", "Germany"): [{"first_name": "Anke", "title": "VP Supply Chain"}]})
        self.assertEqual((contact["name"], contact["location"]), ("Jo", "United Kingdom"))   # location before function
        self.assertEqual(self.searched_places(), ["United Kingdom"])

    def test_without_anyone_in_the_uk_the_next_place_is_tried(self):
        contact = self.find(main={("domain", "Germany"): [{"first_name": "Anke", "title": "Head of Supply Chain"}]})
        self.assertEqual((contact["name"], contact["location"]), ("Anke", "Germany"))
        self.assertEqual(self.searched_places(), ["United Kingdom", "Germany"])

    def test_nothing_anywhere_means_every_place_in_order_then_the_fallback_in_the_same_order(self):
        contact = self.find()
        self.assertEqual(contact["name"], "")
        self.assertIn("no_match", contact["status"])
        self.assertEqual(self.searched_places(), settings.CONTACT_PLACES)
        fallback_places = [call[2][1] for call in self.calls if call[:2] == ("people", "fallback")]
        self.assertEqual(fallback_places, settings.CONTACT_PLACES)
        self.assertNotIn("Turkey", str(self.calls))   # never anywhere else

    def test_fallback_takes_founders_in_the_same_order_of_places(self):
        contact = self.find(fallback={("domain", "Germany"): [{"first_name": "Hans", "title": "Founder"}],
                                      ("domain", "France"): [{"first_name": "Luc", "title": "Founder"}]})
        self.assertEqual((contact["name"], contact["location"]), ("Hans", "Germany"))
        self.assertIn("fallback", contact["status"])
        uk = self.find(fallback={("domain", "United Kingdom"): [{"first_name": "Jo", "title": "Owner"}],
                                 ("domain", "Germany"): [{"first_name": "Hans", "title": "Founder"}]})
        self.assertEqual(uk["name"], "Jo")

    def test_a_function_match_in_a_late_place_beats_a_founder_in_the_uk(self):
        contact = self.find(main={("domain", "Spain"): [{"first_name": "Eva", "title": "Director Logistics"}]},
                            fallback={("domain", "United Kingdom"): [{"first_name": "Jo", "title": "Founder"}]})
        self.assertEqual((contact["name"], contact["location"]), ("Eva", "Spain"))

    def test_company_records_are_used_without_a_domain_filter(self):
        companies = {"United Kingdom": [{"id": "uk1", "name": "Brand UK Ltd", "estimated_num_employees": 50},
                                        {"id": "x9", "name": "Brandon Foundation"}]}
        contact = self.find(companies=companies, main={("orgs", "uk1"): [{"first_name": "Jo", "title": "VP Operations"}]})
        self.assertEqual((contact["name"], contact["company"]), ("Jo", "company Brand UK Ltd"))
        sent = [call.kwargs["json"] for call in self.post.call_args_list if "organization_ids" in call.kwargs["json"]][0]
        self.assertEqual(sent["organization_ids"], ["uk1"])
        self.assertNotIn("q_organization_domains_list", sent)

    def test_companies_sharing_the_brand_name_are_left_out_by_their_domain(self):
        companies = {"United Kingdom": [
            {"id": "c1", "name": "Brandco Potato Crisps", "primary_domain": "brandcrispsco.co.uk", "estimated_num_employees": 400},
            {"id": "g1", "name": "Brandco Golf Club", "primary_domain": "brandcogolf.co.uk", "estimated_num_employees": 40},
            {"id": "f1", "name": "Brandco Home Furnishings", "primary_domain": "brandco.com", "estimated_num_employees": 90}]}
        people = {("orgs", "c1"): [{"first_name": "Jo", "title": "Head of Supply Chain"}]}
        with mock.patch("requests.post", side_effect=self.router(companies=companies, main=people)) as post:
            contact = c.find_contact("brandcrispsco.co.uk", "Brandco")
        sent = [call.kwargs["json"] for call in post.call_args_list if "organization_ids" in call.kwargs["json"]][0]
        self.assertEqual(sent["organization_ids"], ["c1"])       # not the golf club, not the furniture maker
        self.assertEqual(contact["name"], "Jo")
        self.assertIn("(brandcrispsco.co.uk)", contact["company"])

    def test_records_of_the_same_family_are_all_kept(self):
        companies = {"United Kingdom": [
            {"id": "a", "name": "Zanto UK", "primary_domain": "zanto.co.uk", "estimated_num_employees": 200},
            {"id": "b", "name": "Zanto Confectionery", "primary_domain": "zanto.com", "estimated_num_employees": 100},
            {"id": "x", "name": "Zanto Garage", "primary_domain": "zantogarage.com", "estimated_num_employees": 300}]}
        with mock.patch("requests.post", side_effect=self.router(companies=companies)) as post:
            c.find_contact("zanto.de", "Zanto")
        sent = [call.kwargs["json"] for call in post.call_args_list if "organization_ids" in call.kwargs["json"]][0]
        self.assertEqual(sent["organization_ids"], ["a", "b"])   # same domain root: zanto

    def test_without_a_domain_from_apollo_only_the_biggest_record_is_kept(self):
        companies = {"United Kingdom": [{"id": "s", "name": "Brandco Small", "estimated_num_employees": 5},
                                        {"id": "l", "name": "Brandco Large", "estimated_num_employees": 500},
                                        {"id": "m", "name": "Brandco Medium", "estimated_num_employees": 50}]}
        with mock.patch("requests.post", side_effect=self.router(companies=companies)) as post:
            c.find_contact("brandco.com", "Brandco")
        sent = [call.kwargs["json"] for call in post.call_args_list if "organization_ids" in call.kwargs["json"]][0]
        self.assertEqual(sent["organization_ids"], ["l"])

    def test_when_no_record_has_the_brands_domain_no_unrelated_record_is_used(self):
        companies = {"United Kingdom": [{"id": "t1", "name": "Zanto Transport", "primary_domain": "zanto-transport.co.uk",
                                         "estimated_num_employees": 500}]}
        main = {("domain", "United Kingdom"): [{"first_name": "Jo", "title": "Head of Logistics"}]}
        with mock.patch("requests.post", side_effect=self.router(companies=companies, main=main)) as post:
            contact = c.find_contact("zanto.co.uk", "Zanto")
        bodies = [call.kwargs["json"] for call in post.call_args_list if "mixed_companies" not in call.args[0]]
        self.assertTrue(all("organization_ids" not in body for body in bodies))   # the transport firm is never read
        self.assertEqual(contact["name"], "Jo")
        self.assertTrue(contact["company"].startswith("domain zanto.co.uk"))

    def test_a_matching_record_without_anyone_falls_back_on_the_domain(self):
        companies = {"United Kingdom": [{"id": "n1", "name": "Crispo Potato Crisps", "primary_domain": "crispo.nl"}]}
        main = {("domain", "United Kingdom"): [{"first_name": "Sam", "title": "Sales Manager"}]}
        with mock.patch("requests.post", side_effect=self.router(companies=companies, main=main)):
            contact = c.find_contact("crispo.co.uk", "Crispo")
        self.assertEqual(contact["name"], "Sam")
        asked = [call[2] for call in self.calls if call[0] == "people"]
        self.assertEqual(asked[:2], [("orgs", "n1"), ("domain", "United Kingdom")])   # the record first, then the domain

    def test_the_founder_fallback_also_tries_the_domain(self):
        companies = {"United Kingdom": [{"id": "n1", "name": "Crispo Potato Crisps", "primary_domain": "crispo.nl"}]}
        fallback = {("domain", "United Kingdom"): [{"first_name": "Max", "title": "Founder"}]}
        with mock.patch("requests.post", side_effect=self.router(companies=companies, fallback=fallback)):
            contact = c.find_contact("crispo.co.uk", "Crispo")
        self.assertEqual((contact["name"], contact["location"]), ("Max", "United Kingdom"))
        self.assertIn("fallback", contact["status"])

    def test_the_domain_root(self):
        for domain, root in [("www.tyrrellscrisps.co.uk", "tyrrellscrisps"), ("fever-tree.com", "fevertree"),
                             ("https://shop.brand.de/en", "brand"), ("", "")]:
            self.assertEqual(c.domain_root(domain) if domain else "", root, domain)

    def test_the_request_asks_for_our_functions_and_levels(self):
        self.find()
        body = [call.kwargs["json"] for call in self.post.call_args_list if "person_titles" in call.kwargs["json"]][0]
        self.assertEqual(body["person_titles"], settings.CONTACT_FUNCTIONS)
        self.assertEqual(body["person_seniorities"], settings.APOLLO_SENIORITY_MAIN)
        self.assertEqual((body["per_page"], body["person_locations"], body["q_organization_domains_list"]),
                         (100, ["United Kingdom"], ["brand.com"]))

    def test_next_page_only_when_the_page_is_full_and_has_no_perfect_match(self):
        pages = {1: [{"first_name": f"J{i}", "title": "Supply Chain Analyst"} for i in range(3)],
                 2: [{"first_name": "Eva", "title": "Head of Logistics"}]}

        def post(url, **kw):
            if "mixed_companies" in url:
                return response(organizations=[])
            self.calls.append(kw["json"].get("page"))
            return response(people=pages.get(kw["json"]["page"], []))
        with mock.patch.object(settings, "CONTACT_PAGE_SIZE", 3), mock.patch("requests.post", side_effect=post):
            contact = c.find_contact("brand.com", "Brand")
        self.assertEqual((contact["name"], self.calls), ("Eva", [1, 2]))

    def test_a_perfect_match_stops_the_paging(self):
        full = [{"first_name": "Eva", "title": "VP Supply Chain"}, {"first_name": "A", "title": "x"}, {"first_name": "B", "title": "y"}]

        def post(url, **kw):
            if "mixed_companies" in url:
                return response(organizations=[])
            self.calls.append(kw["json"]["page"])
            return response(people=full)
        with mock.patch.object(settings, "CONTACT_PAGE_SIZE", 3), mock.patch("requests.post", side_effect=post):
            c.find_contact("brand.com", "Brand")
        self.assertEqual(self.calls, [1])

    def test_paging_is_capped(self):
        def post(url, **kw):
            if "mixed_companies" in url:
                return response(organizations=[])
            if "person_titles" in kw["json"] and kw["json"]["person_locations"] == ["United Kingdom"]:
                self.calls.append(kw["json"]["page"])
            return response(people=[{"first_name": "J", "title": "Supply Chain Analyst"}] * 2)
        with mock.patch.object(settings, "CONTACT_PAGE_SIZE", 2), mock.patch.object(settings, "CONTACT_MAX_PAGES", 2), \
             mock.patch("requests.post", side_effect=post):
            c.find_contact("brand.com", "Brand")
        self.assertEqual(self.calls, [1, 2])

    def test_a_rate_limit_is_retried(self):
        answers = iter([response(429), response(200, people=[{"first_name": "Jo", "title": "Head of Logistics"}])])
        with mock.patch("requests.post", side_effect=lambda url, **kw: response(organizations=[]) if "mixed_companies" in url else next(answers)):
            contact = c.find_contact("brand.com", "Brand")
        self.assertEqual(contact["name"], "Jo")

    def test_a_bad_request_costs_only_that_lead_but_a_refusal_stops_everything(self):
        with mock.patch("requests.post", side_effect=lambda url, **kw: response(organizations=[]) if "mixed_companies" in url
                        else response(422, text='{"error":"bad location"}')):
            first = c.find_contact("a.com", "A")
        self.assertIn("error: Apollo answered 422", first["status"])
        self.assertEqual(c.APOLLO_PEOPLE_BLOCKED["reason"], "")          # not blocked: the next lead is searched

        with mock.patch("requests.post", return_value=response(403, text='{"error":"master key needed"}')) as post:
            refused = c.find_contact("b.com", "B")
            calls_after = post.call_count
            skipped = c.find_contact("c.com", "C")
        self.assertIn("error 403", refused["status"])
        self.assertIn("skipped after access error", skipped["status"])
        self.assertEqual(post.call_count, calls_after)

    def test_candidates_say_why_each_person_was_or_was_not_chosen(self):
        contact = self.find(main={("domain", "United Kingdom"): [
            {"first_name": "Marc", "title": "Chief Financial Officer"}, {"first_name": "Ida", "title": "CEO"},
            {"first_name": "Zoe", "title": "Product Manager"}, {"first_name": "Tom", "title": "Head of Supply Chain"}]})
        labels = contact["candidates"]
        for expected in ("Chief Financial Officer @ United Kingdom [excluded: finance]", "CEO @ United Kingdom [C-level or founder",
                         "Product Manager @ United Kingdom [excluded: product/design]", "Head of Supply Chain @ United Kingdom [PICKED]"):
            self.assertIn(expected, labels)

    def test_the_email_is_never_requested_by_default(self):
        self.find(main={("domain", "United Kingdom"): [{"id": "p1", "first_name": "Jo", "title": "VP Operations"}]})
        self.assertFalse(settings.REVEAL_CONTACT_EMAIL)
        self.assertTrue(all("people/match" not in call.args[0] for call in self.post.call_args_list))

    def test_guards(self):
        with mock.patch.object(settings, "APOLLO_API_KEY", ""):
            self.assertIn("not_configured", c.find_contact("a.com", "A")["status"])
        self.assertEqual(c.find_contact("", "A")["status"], "no_domain")
        with mock.patch.object(settings, "CONTACT_PLACES", []):
            self.assertIn("no_place", c.find_contact("a.com", "A")["status"])


if __name__ == "__main__":
    unittest.main()