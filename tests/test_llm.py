import unittest
from unittest import mock

from leadrouter import llm, settings


class TargetDefinitionTest(unittest.TestCase):
    def prompt(self):
        return llm.build_prompt("Brand", "https://brand.example", "Some text from the website.")

    def test_the_prompt_uses_the_definition_from_the_settings(self):
        self.assertIn(settings.TARGET_BRAND_DEFINITION, self.prompt())

    def test_changing_the_definition_changes_the_prompt(self):
        with mock.patch.object(settings, "TARGET_BRAND_DEFINITION", "a company that makes garden tools"):
            self.assertIn("a company that makes garden tools", self.prompt())

    def test_the_schema_describes_the_same_definition(self):
        description = llm.LeadEnrichment.model_fields["sells_own_physical_goods"].description
        self.assertIn(settings.TARGET_BRAND_DEFINITION, description)

    def test_the_prompt_defines_the_target_instead_of_listing_excluded_cases(self):
        prompt = self.prompt().lower()
        for excluded_case in ("restaurant", "bank", "caf\u00e9"):
            self.assertNotIn(excluded_case, prompt)


if __name__ == "__main__":
    unittest.main()