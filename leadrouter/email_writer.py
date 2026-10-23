"""The cold email: written by the model from a handful of facts, then checked by plain rules."""

import re

from . import settings
from .llm import EmailDraft, generate_structured
from .textutils import normalize_text


def build_email_prompt(company: str, country: str, summary: str, verified: list[str],
                       problems: list[str] | None = None, contact_first_name: str = "") -> str:
    greeting_name = contact_first_name or f"{company} team"
    fix = ""
    if problems:
        fix = "\nYour previous draft had these problems. Fix all of them:\n- " + "\n- ".join(problems) + "\n"
    # `summary` and `verified` are deliberately not given to the model: the email names no retailer and
    # does not describe the products. They stay in the signature so the rest of the script is unchanged.
    return f"""
You write one short cold email from the go-to-market team of {settings.SENDER_COMPANY} to a consumer brand.

What {settings.SENDER_COMPANY} does: {settings.PRODUCT_PITCH}

Who we write to: a brand that already sells through retailers. It is probably already connected to the retailers
it sells to, so never suggest it has a problem with those. Our offer is about the NEXT retailers or markets it adds:
each new one is normally a new integration project.

Facts about the brand. These are the only facts you may use:
- Brand: {company}
- Country: {country or "unknown"}

Structure:
1. Greeting: "Hi {greeting_name},"
2. One sentence: every new retailer usually asks for orders and invoices in its own format, so each new connection
   tends to become a separate integration project. Present it as a likely situation, never as a fact about them.
3. One sentence: what {settings.SENDER_COMPANY} does, framed around adding retailers: the supplier connects its ERP once,
   then adds retailers without a new project each time.
4. One question, asking whether they plan to add retailers or markets soon, and offering a short call if so.
   Only one question mark in the whole email.
5. Sign-off: "Best," then a line with "[Name], {settings.SENDER_COMPANY}".

Layout of the body: real line breaks, one blank line between blocks, exactly like this:
Hi {greeting_name},
(blank line)
Points 2 and 3 as one short paragraph.
(blank line)
Point 4, the question.
(blank line)
Best,
[Name], {settings.SENDER_COMPANY}

Length: 45 to 70 words in total, greeting and sign-off included.

Rules:
- Write in {settings.EMAIL_LANGUAGE}.
- Use only the facts above. Invent no numbers, no customers, no results, no plans of the brand.
- Do not name any retailer or supermarket chain. Do not say what the brand makes or sells.
- Plain text only: no bold, no bullet points, no emojis, no exclamation marks, no dashes (use commas or full stops).
- No flattery and no filler. Never write: "I hope this finds you well", "I came across", "I wanted to",
  "I would love to", "reaching out", "touch base", "streamline", "leverage", "seamless", "game-changing".
- No list of three adjectives, no "not just X, but Y", no closing summary sentence, no remark like "it is worth noting".
{fix}""".strip()

def format_body(body: str) -> str:
    """Puts the email in shape whatever the model returned: real line breaks, the greeting alone on its line,
    a blank line between blocks, the sign-off on its own lines. A body that already has line breaks is kept."""
    text = body.replace("\\n", "\n").replace("\r\n", "\n").strip()   # "\n" typed as text, Windows line breaks
    text = re.sub(r"[ \t]+\n", "\n", text)                              # no trailing spaces
    if "\n" not in text:                                                 # the model wrote one single line
        text = re.sub(r"^((?:Hi|Hello|Dear) [^,\n]{1,40},)\s+", r"\1\n\n", text)        # greeting alone
        text = re.sub(r"\s+(Best,)\s*", r"\n\n\1\n", text)                           # sign-off
        text = re.sub(r"(?<=[.])[ ]+([^.?!\n]*\?)", r"\n\n\1", text, count=1)        # the question
    return re.sub(r"\n{3,}", "\n\n", text)                                 # one blank line at most


def layout_problems(body: str) -> list[str]:
    if body.count("\n") < 4:
        return ["has no line breaks: put the greeting, the paragraph, the question and the sign-off on separate lines"]
    return []

def check_email(subject: str, body: str, verified: list[str]) -> list[str]:
    """Plain-rule checks. Returns a list of problems (empty if the email is clean)."""
    problems = []
    text = f"{subject}\n{body}"
    low = text.lower()

    for phrase in settings.BANNED_PHRASES:
        if phrase in low:
            problems.append(f'uses the banned phrase "{phrase}"')
    if "!" in text:
        problems.append("uses an exclamation mark")
    if re.search(r"\d", text):
        problems.append("contains a number, but no number was given in the facts")
    if "**" in text or re.search(r"(?m)^\s*[-*•]\s", body):
        problems.append("uses formatting (bold or bullet points)")
    if body.count("?") > 1:
        problems.append("asks more than one question")
    words = len(body.split())
    if words > settings.MAX_EMAIL_WORDS:
        problems.append(f"is too long ({words} words, maximum {settings.MAX_EMAIL_WORDS})")

    # A retailer name from our list that appears in the email but was not verified for this brand
    verified_keys = {normalize_text(v) for v in verified}
    body_norm = normalize_text(body)
    all_names = {name for chains in settings.RETAILER_DOMAINS.values() for name in chains.values()}
    for name in sorted(all_names):
        key = normalize_text(name)
        if key in verified_keys:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", body_norm):
            problems.append(f"names the retailer {name}, but no retailer may be named in the email")
    return problems


def generate_email(company: str, country: str, summary: str, verified: list[str],
                   contact_first_name: str = "") -> dict:
    """Writes the email, checks it with plain rules, and retries once with the list of problems."""
    problems: list[str] = []
    draft = None
    for _ in range(2):
        draft = generate_structured(build_email_prompt(company, country, summary, verified, problems or None,
                                                    contact_first_name=contact_first_name), EmailDraft)
        # Dashes are replaced by commas without asking the model again
        draft.subject = re.sub(r"[ \t]*[—–][ \t]*", ", ", draft.subject).strip()
        draft.body = format_body(re.sub(r"[ \t]*[—–][ \t]*", ", ", draft.body))
        problems = check_email(draft.subject, draft.body, []) + layout_problems(draft.body)   # no retailer allowed        
        if not problems:
            break
    return {"subject": draft.subject, "body": draft.body, "flags": problems}
