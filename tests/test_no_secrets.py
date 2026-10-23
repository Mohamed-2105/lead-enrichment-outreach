"""Guards the promise of the project: no key, no personal address, no private file can end up in the code."""

import ast
import contextlib
import io
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from leadrouter import settings
from leadrouter.log import log

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", "output", "drafts_eml", ".venv", "venv"}
PRIVATE_FILES = {".env", "credentials.json", "token.json", "leads_input.csv"}   # ignored by git, may hold real data
TEXT_SUFFIXES = {".py", ".md", ".txt", ".csv", ".example", ".json", ""}
ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org"}
OBVIOUSLY_FAKE = ("fake", "for-tests", "your", "example", "placeholder")   # values used by the tests

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),                               # Google API keys
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),                                 # common secret-key prefix
    re.compile(r"\d{10,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com"),   # Google OAuth client id
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"),
]


def private_words() -> set[str]:
    """Names that must never appear in the public files. They come from your private .env, never from this file:
    PRIVATE_WORDS=name1,name2 (comma separated), plus the company written in SENDER_COMPANY."""
    words = {w.strip().lower() for w in os.environ.get("PRIVATE_WORDS", "").split(",") if w.strip()}
    sender = os.environ.get("SENDER_COMPANY", "").strip().lower()
    if sender and sender != "acme connect":
        words.add(sender)
    return words


def project_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.name in PRIVATE_FILES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {".gitignore", ".env.example"}:
            yield path


class NoSecretsInTheCodeTest(unittest.TestCase):
    def test_no_personal_email_address_anywhere(self):
        found = []
        for path in project_files():
            for match in EMAIL.finditer(path.read_text(encoding="utf-8", errors="ignore")):
                if match.group(0).split("@")[1].lower() not in ALLOWED_EMAIL_DOMAINS:
                    found.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
        self.assertEqual(found, [], "only example.com addresses may appear in the project")

    def test_no_key_like_value_anywhere(self):
        found = []
        for path in project_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in KEY_PATTERNS:
                for match in pattern.finditer(text):
                    if not any(mark in match.group(0).lower() for mark in OBVIOUSLY_FAKE):
                        found.append(f"{path.relative_to(ROOT)}: {pattern.pattern[:30]}")
        self.assertEqual(found, [])

    def test_no_private_name_in_the_public_files(self):
        """The company the project was written for lives in your private .env, never in the repository."""
        words = private_words()
        if not words:
            self.skipTest("no private names configured: set PRIVATE_WORDS (and SENDER_COMPANY) in your .env")
        found = []
        for path in project_files():
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            found += [f"{path.relative_to(ROOT)}: {word}" for word in words if word in text]
        self.assertEqual(found, [])

    def test_settings_reads_every_secret_from_the_environment(self):
        tree = ast.parse((ROOT / "leadrouter" / "settings.py").read_text(encoding="utf-8"))
        assigned = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                assigned[node.targets[0].id] = node.value
        for name in settings.SECRET_NAMES:
            self.assertIn(name, assigned)
            self.assertNotIsInstance(assigned[name], ast.Constant, f"{name} must not be a literal")

    def test_private_files_are_ignored_by_git(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for pattern in (".env", "credentials.json", "token.json", "output/", "data/leads_input.csv"):
            self.assertIn(pattern, ignored)
        self.assertTrue((ROOT / ".env.example").is_file())

    def test_the_env_example_holds_placeholders_only(self):
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                value = line.split("=", 1)[1].strip()
                self.assertTrue(value == "" or "your" in value or value.endswith("example.com"), line)


class RedactionTest(unittest.TestCase):
    def test_redact_hides_values_from_the_environment_and_from_settings(self):
        with mock.patch.dict(os.environ, {"SERPER_API_KEY": "env-secret-111111"}), \
             mock.patch.object(settings, "APOLLO_API_KEY", "attr-secret-222222"), \
             mock.patch.object(settings, "DRAFT_RECIPIENT", "me@example.com"):
            text = settings.redact("a env-secret-111111 b attr-secret-222222 c me@example.com d")
        self.assertEqual(text, "a [hidden] b [hidden] c [hidden] d")

    def test_log_never_prints_a_secret(self):
        out = io.StringIO()
        with mock.patch.object(settings, "GEMINI_API_KEY", "gem-secret-333333"), contextlib.redirect_stdout(out):
            log("request failed for key gem-secret-333333")
        self.assertNotIn("gem-secret-333333", out.getvalue())

    def test_the_service_overview_shows_no_value(self):
        with mock.patch.object(settings, "GEMINI_API_KEY", "gem-secret-333333"), \
             mock.patch.object(settings, "SERPER_API_KEY", ""), mock.patch.object(settings, "APOLLO_API_KEY", ""):
            overview = settings.configured_services()
        self.assertEqual(list(overview.values()), [True, False, False])
        self.assertNotIn("gem-secret-333333", str(overview))


if __name__ == "__main__":
    unittest.main()