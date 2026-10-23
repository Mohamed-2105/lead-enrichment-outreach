import unittest
from unittest import mock

from leadrouter import email_writer as e
from leadrouter import settings
from leadrouter.llm import EmailDraft

GOOD = ("Hi Claire,\n\nEach new retailer usually asks for orders and invoices in its own format, so every connection "
        f"tends to become a separate project. {settings.SENDER_COMPANY} lets a supplier connect its ERP once, then add retailers without "
        "a new project each time. Are you planning to add retailers or markets soon? If so, a short call could be "
        f"useful.\n\nBest,\n[Name], {settings.SENDER_COMPANY}")


class PromptTest(unittest.TestCase):
    def test_the_model_never_sees_retailers_or_what_the_brand_makes(self):
        prompt = e.build_email_prompt("Zanto", "DE", "Fruit gummies and liquorice.", ["Rewe", "Edeka"], contact_first_name="Claire")
        for word in ("Rewe", "Edeka", "gummies", "liquorice"):
            self.assertNotIn(word, prompt)
        self.assertIn("Hi Claire,", prompt)
        self.assertIn("Do not name any retailer", prompt)
        self.assertIn("real line breaks", prompt)

    def test_greeting_without_a_contact(self):
        self.assertIn("Hi Zanto team,", e.build_email_prompt("Zanto", "DE", "", []))


class CheckTest(unittest.TestCase):
    def test_a_clean_email_has_no_problem(self):
        self.assertEqual(e.check_email("Adding retailers at Zanto", GOOD, []), [])
        self.assertLess(len(GOOD.split()), e.settings.MAX_EMAIL_WORDS)

    def test_each_rule(self):
        bad = ("Hi Plumo team,\nI hope this finds you well! Plumo is sold at Carrefour and Tesco, with 300 stores.\n"
               "- first\nOpen to a call? Or two?\nBest")
        problems = " | ".join(e.check_email("Great news!", bad, []))
        for expected in ("banned phrase", "exclamation", "number", "formatting", "more than one question",
                         "names the retailer Carrefour", "names the retailer Tesco"):
            self.assertIn(expected, problems)

    def test_a_short_chain_name_does_not_match_inside_another_word(self):
        self.assertEqual(e.check_email("s", "Hi, our media team can help.", []), [])


class LayoutTest(unittest.TestCase):
    def test_one_single_line_is_put_in_shape(self):
        shaped = e.format_body(GOOD.replace("\n", " "))
        self.assertTrue(shaped.startswith("Hi Claire,\n\n"))
        self.assertTrue(shaped.endswith(f"\n\nBest,\n[Name], {settings.SENDER_COMPANY}"))
        self.assertEqual(shaped.count("\n\n"), 3)            # greeting | paragraph | question | sign-off
        self.assertIn("\n\nAre you planning", shaped)
        self.assertEqual(e.layout_problems(shaped), [])

    def test_a_body_that_is_already_laid_out_is_left_alone(self):
        self.assertEqual(e.format_body(GOOD), GOOD)

    def test_line_breaks_typed_as_text_become_real_ones(self):
        self.assertEqual(e.format_body("Hi Claire,\\n\\nText.\\n\\nBest,\\n[Name], X"), "Hi Claire,\n\nText.\n\nBest,\n[Name], X")

    def test_too_many_blank_lines_are_reduced(self):
        self.assertEqual(e.format_body("Hi A,\n\n\n\nText.\n\n\n\nBest,\nX"), "Hi A,\n\nText.\n\nBest,\nX")

    def test_a_body_without_line_breaks_is_flagged(self):
        self.assertTrue(e.layout_problems("Hi Claire, one long line. Best, [Name], X"))


class GenerateTest(unittest.TestCase):
    def test_a_single_line_answer_from_the_model_comes_out_laid_out_and_unflagged(self):
        one_line = EmailDraft(subject="Adding retailers at Zanto", body=GOOD.replace("\n", " "))
        with mock.patch.object(e, "generate_structured", return_value=one_line):
            result = e.generate_email("Zanto", "DE", "x", [], contact_first_name="Claire")
        self.assertEqual(result["flags"], [])
        self.assertIn("Hi Claire,\n\n", result["body"])
        self.assertGreaterEqual(result["body"].count("\n"), 5)

    def test_retry_feeds_the_problems_back_and_dashes_are_replaced(self):
        bad = EmailDraft(subject="Zanto and Rewe", body=GOOD.replace("Each new", "Zanto is sold at Rewe. Each new"))
        good = EmailDraft(subject="Adding retailers at Zanto \u2014 soon", body=GOOD)
        prompts = []

        def fake(prompt, schema, temperature=0.3):
            prompts.append(prompt)
            return [bad, good][len(prompts) - 1]

        with mock.patch.object(e, "generate_structured", side_effect=fake):
            result = e.generate_email("Zanto", "DE", "x", ["Rewe"], contact_first_name="Claire")
        self.assertEqual(len(prompts), 2)
        self.assertIn("no retailer may be named", prompts[1])
        self.assertEqual(result["flags"], [])
        self.assertNotIn("\u2014", result["subject"])

    def test_a_model_that_keeps_failing_is_flagged_not_hidden(self):
        bad = EmailDraft(subject="Hi!", body="I hope this finds you well! Rewe loves us.")
        with mock.patch.object(e, "generate_structured", return_value=bad):
            result = e.generate_email("X", "FR", "x", [])
        self.assertTrue(result["flags"])


if __name__ == "__main__":
    unittest.main()