import base64
import email
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from leadrouter import gmail_drafts as g
from leadrouter import settings

RECIPIENT = "me@example.com"
ROW = {"company_name": "Brand A", "website_url": "https://brand-a.fr", "score": 8,
       "routing_status": "Tier 1 - Hot Lead (Sequence A)", "retailers_verified": "Carrefour | Monoprix",
       "retailers_verified_sources": "Carrefour (https://carrefour.fr/p/1)", "contact_name": "Claire",
       "contact_title": "VP Operations", "contact_email": float("nan"), "email_subject": "Orders from Carrefour",
       "cold_email_draft": "Hi Claire,\n\nYour biscuits are sold at Carrefour.\n\nBest,\n[Name], Acme Connect",
       "email_flags": float("nan")}


class MessageTest(unittest.TestCase):
    def test_the_draft_is_addressed_to_the_recipient_given_and_says_it_is_a_demo(self):
        message = g.build_message(RECIPIENT, ROW)
        self.assertEqual(message["To"], RECIPIENT)
        self.assertEqual(message["X-Unsent"], "1")
        self.assertTrue(message["Subject"].startswith("[DEMO]"))
        body = message.get_content()
        self.assertIn("DEMO DRAFT. Not sent to anyone.", body)
        self.assertIn("Suggested contact: Claire - VP Operations", body)
        self.assertIn("Hi Claire,", body)
        self.assertNotIn("nan", body.lower().replace("finance", ""))

    def test_raw_form_for_the_gmail_api(self):
        parsed = email.message_from_bytes(base64.urlsafe_b64decode(g.build_raw_message(RECIPIENT, ROW)))
        self.assertEqual(parsed["To"], RECIPIENT)


class DraftsTest(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([
            ROW,
            {**ROW, "company_name": "Soft", "routing_status": "Tier 3 - Disqualified", "cold_email_draft": ""},
            {**ROW, "company_name": "Mid", "routing_status": "Tier 2 - Manual Review (Sequence B)"},
        ])

    def test_only_tier_one_gets_a_draft_and_a_rerun_creates_no_duplicate(self):
        service = mock.MagicMock()
        service.users().drafts().create().execute.return_value = {"id": "d1"}
        with tempfile.TemporaryDirectory() as folder:
            log_file = Path(folder) / "drafts_log.json"
            first = g.create_drafts(service, RECIPIENT, self.frame(), log_file)
            second = g.create_drafts(service, RECIPIENT, self.frame(), log_file)
        self.assertEqual((first, second), (1, 0))

    def test_nothing_is_ever_sent(self):
        service = mock.MagicMock()
        service.users().drafts().create().execute.return_value = {"id": "d1"}
        with tempfile.TemporaryDirectory() as folder:
            g.create_drafts(service, RECIPIENT, self.frame(), Path(folder) / "log.json")
        self.assertFalse(service.users.return_value.messages.called)
        self.assertFalse(service.users.return_value.drafts.return_value.send.called)

    def test_eml_files_need_no_google_account(self):
        with tempfile.TemporaryDirectory() as folder:
            count = g.write_eml_files(RECIPIENT, self.frame(), Path(folder) / "drafts_eml")
            names = sorted(p.name for p in (Path(folder) / "drafts_eml").iterdir())
        self.assertEqual((count, names), (1, ["brand_a.eml"]))

    def test_the_recipient_is_mandatory(self):
        with mock.patch.object(settings, "DRAFT_RECIPIENT", ""):
            with self.assertRaises(SystemExit):
                g.main([])


if __name__ == "__main__":
    unittest.main()
