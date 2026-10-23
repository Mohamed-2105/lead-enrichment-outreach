"""Demo drafts of the emails, never sent.

Every draft is addressed to YOUR OWN address (DRAFT_RECIPIENT in .env). The real target appears at the top of the
draft. This module only creates drafts and never calls Gmail's send function. Note: Google's "compose" permission
technically also covers sending, so keep token.json private.

Two modes:
  * Gmail drafts (needs a Google OAuth client, see README):  python3 create_drafts.py
  * .eml files, no Google account needed:                    python3 create_drafts.py --eml
"""

import base64
import json
import re
import sys
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from . import settings
from .log import log
from .scoring import MAX_SCORE


SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]   # compose drafts only
CREDENTIALS_FILE = settings.PROJECT_ROOT / "credentials.json"   # downloaded from Google Cloud (never committed)
TOKEN_FILE = settings.PROJECT_ROOT / "token.json"               # created on first run (never committed)
LOG_FILE = settings.PROJECT_ROOT / "output" / "drafts_log.json"   # remembers which drafts exist


def clean(value) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def build_message(recipient: str, row: dict) -> EmailMessage:
    """The draft: a short DEMO header (who the real target is, why this lead), a separator, then the email."""
    contact_line = " - ".join(x for x in [clean(row.get("contact_name")), clean(row.get("contact_title"))] if x)
    contact_email = clean(row.get("contact_email"))
    header = [
        "DEMO DRAFT. Not sent to anyone. Addressed to yourself on purpose.",
        f"Suggested contact: {contact_line or 'none found'}" + (f" <{contact_email}>" if contact_email else ""),
        f"Company: {clean(row.get('company_name'))} ({clean(row.get('website_url'))})",
        f"Score: {clean(row.get('score'))}/{MAX_SCORE}|  {clean(row.get('routing_status'))}",
        f"Retailers verified: {clean(row.get('retailers_verified')) or 'none'}",
        f"Sources: {clean(row.get('retailers_verified_sources')) or 'none'}",
    ]
    flags = clean(row.get("email_flags"))
    if flags:
        header.append(f"Email flags: {flags}")

    message = EmailMessage()
    message["To"] = recipient
    message["X-Unsent"] = "1"   # mail apps that understand it open the file as an unsent draft
    message["Subject"] = f"[DEMO] {clean(row.get('email_subject')) or clean(row.get('company_name'))}"
    message.set_content("\n".join(header) + "\n\n" + "-" * 40 + "\n\n" + clean(row.get("cold_email_draft")))
    return message


def build_raw_message(recipient: str, row: dict) -> str:
    return base64.urlsafe_b64encode(build_message(recipient, row).as_bytes()).decode()


def is_eligible(row: dict) -> bool:
    return clean(row.get("routing_status")).startswith(settings.DRAFT_TIERS) and bool(clean(row.get("cold_email_draft")))


def write_eml_files(recipient: str, df: pd.DataFrame, folder: Path) -> int:
    """One .eml file per lead in `folder` (double-click to open). No Google account needed."""
    folder.mkdir(parents=True, exist_ok=True)
    count = 0
    for row in df.to_dict("records"):
        if not is_eligible(row):
            continue
        name = re.sub(r"[^a-z0-9]+", "_", clean(row.get("company_name")).lower()).strip("_") or "lead"
        (folder / f"{name}.eml").write_bytes(build_message(recipient, row).as_bytes())
        count += 1
        log(f"  file written: {name}.eml")
    return count


def get_service():
    """Signs in to Gmail (browser window on the first run) and returns the API client."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.is_file():
                raise SystemExit("credentials.json not found. See the Gmail section of the README.")
            creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES).run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def create_drafts(service, recipient: str, df: pd.DataFrame, log_file: Path = LOG_FILE) -> int:
    """One Gmail draft per eligible lead. A lead that already has a draft is skipped."""
    done = json.loads(log_file.read_text(encoding="utf-8")) if log_file.is_file() else {}
    created = 0
    for row in df.to_dict("records"):
        company = clean(row.get("company_name"))
        if not is_eligible(row):
            continue
        if company in done:
            log(f"  skipped (draft already exists): {company}")
            continue
        draft = service.users().drafts().create(
            userId="me", body={"message": {"raw": build_raw_message(recipient, row)}}
        ).execute()
        done[company] = draft.get("id", "")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps(done, indent=2), encoding="utf-8")
        created += 1
        log(f"  draft created: {company}")
    return created


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not settings.DRAFT_RECIPIENT:
        raise SystemExit("Add DRAFT_RECIPIENT=your.address@... to your .env file (your own address).")
    output = Path(settings.OUTPUT_CSV)
    if not output.is_file():
        raise SystemExit(f"{output.name} not found. Run the pipeline first: python3 run.py")

    df = pd.read_csv(output)
    eligible = sum(is_eligible(r) for r in df.to_dict("records"))
    log(f"{eligible} lead(s) eligible. Drafts are addressed to your own address (DRAFT_RECIPIENT). Nothing is sent.\n")
    if "--eml" in argv:
        count = write_eml_files(settings.DRAFT_RECIPIENT, df, output.parent / "drafts_eml")
        log(f"\nDone: {count} .eml file(s) in the drafts_eml folder. Double-click one to open it.")
        return
    count = create_drafts(get_service(), settings.DRAFT_RECIPIENT, df)
    log(f"\nDone: {count} draft(s) created. Open Gmail > Drafts.")
