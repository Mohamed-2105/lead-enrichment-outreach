# Lead Router

Takes a list of consumer brands and, for each one, finds out who sells it, how big it is and whether it is worth
contacting. It scores the brand, finds a contact person, and drafts a short cold email. **Nothing is ever sent**:
the result is a CSV, plus optional demo drafts addressed to yourself.

It started as a prototype to learn go-to-market engineering. The example it is written around is a (fictional)
B2B platform, "Acme Connect", that lets a supplier connect its ERP once and exchange orders, invoices and delivery
notes with every retailer. To aim it at your own company, set `SENDER_COMPANY` (and optionally `PRODUCT_PITCH`) in
`.env`: the name is a setting, nothing else in the code mentions it.

## How it works

```
company name ──► website (CSV, or web search + checks) ──► read the site + web searches
      ──► Gemini extracts facts (is it a target brand? retailers named? country?)
      ──► plain rules verify them (a retailer name must appear in the text, a headcount must be written)
      ──► score /10 and tier ──► contact via Apollo (UK first, then Germany, Switzerland...) ──► email (Gemini + rule checks)
      ──► one CSV row, saved after every company
```

Principle: **the model extracts, rules decide, every claim is checked or flagged.** The score is arithmetic you can
read in `score_breakdown`; the model never ranks anyone. What counts as a target brand is one sentence in
`settings.py` (`TARGET_BRAND_DEFINITION`), so there is no list of excluded cases to maintain.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then fill in your own keys. Only GEMINI_API_KEY is required
cp data/leads_example.csv data/leads_input.csv     # only company_name is required in the CSV
```

## Run

```bash
python3 run.py --max-leads 4        # a small test first
python3 run.py                      # the whole list; results in output/leads_enriched.csv
python3 create_drafts.py --eml      # demo drafts as .eml files (or without --eml for Gmail drafts, see below)
python3 -m unittest discover -s tests -t . -b    # no key and no network needed: the tests use invented companies
```

Columns worth reading first in the result: `routing_status`, `score_breakdown`, `retailers_verified_sources`
(a link for every retailer found), `contact_candidates` (everyone Apollo returned and what was decided),
`email_flags`, and the `*_status` columns, which say why something is empty instead of leaving a blank.

## How the website is found

If the CSV has no address, the program searches the web and keeps a result only if all three checks pass: it is not
a social network, a database or a marketplace; the company name is in the domain or on the page (connectors such as
"et", "&" and "and" are ignored); and a model confirms that the page belongs to this company and not to a bigger one
sharing a word. A page that blocks robots is accepted at `medium` confidence only if its domain spells the full name.

## How a brand is scored (out of 10)

| Signal | Points |
|---|---|
| Sells its own physical goods | +3 |
| Retailers verified: 3 or more / 1 or 2 / claimed but not proven | +3 / +2 / +1 |
| The brand is from the UK | +2 |
| 200+ employees / 50 to 199 | +2 / +1 |

Tier 1 from 7 points, and only with some sign that the brand sells through retailers and a website that could be
read. Tier 2 (manual review) from 3, Tier 3 below. Tier 1 leads get a contact and an email, Tier 2 leads an email
only. If a website cannot be read, the brand is scored from web search results only and held at Tier 2 at most.
Every point is listed in `score_breakdown`.

## How the contact is chosen

Apollo's people search (no credits, no model). The places are tried in order (UK, Germany, Switzerland, Austria,
France, Italy, Spain) and the first place with a suitable person wins. In each place the search looks first at the
brand's own company records, kept only when their domain has the same root as the brand's website (unrelated
companies often share a name), then at the brand's domain. A suitable person works in a function that receives
integration requests (supply chain first, then logistics, operations...) at VP, head or director level (a manager if
nobody senior), never in finance or marketing and never a C-level. If nobody is found anywhere, it falls back on
founders, in the same order of places. When Apollo attaches the people to a group's record instead of the brand's,
nobody is found: the program prefers no contact to a wrong one. `contact_candidates` shows who was considered and why.

## Gmail drafts (optional)

Create an OAuth client of type "Desktop app" in Google Cloud, enable the Gmail API, save the downloaded file as
`credentials.json` here, add `DRAFT_RECIPIENT` (your own address) to `.env`, then run `python3 create_drafts.py`.
Without any Google setup, `--eml` writes one file per lead in `output/drafts_eml` instead.

## Code map

| File | Role |
|---|---|
| `leadrouter/settings.py` | every setting and the only place secrets are read (from the environment) |
| `leadrouter/pipeline.py` | the run, one small function per step |
| `leadrouter/web.py`, `website.py` | reading sites, web search, finding a missing website |
| `leadrouter/llm.py` | Gemini: schemas, prompts, retries, fallback model |
| `leadrouter/retail.py` | retailer verification, chain-site proofs, headcount |
| `leadrouter/contacts.py` | Apollo contact search, title rules, geography |
| `leadrouter/scoring.py`, `email_writer.py` | the score; the email and its checks |
| `leadrouter/gmail_drafts.py` | demo drafts (Gmail API or `.eml`), never sends |
| `leadrouter/textutils.py`, `log.py` | name and country helpers; printing that never shows a secret |
| `run.py`, `create_drafts.py` | the two commands you run |
| `tools/` | two small scripts that show exactly what Apollo answers |
| `tests/` | the test suite |

## Security and privacy

- Keys and your address live **only** in `.env`, which git ignores. The code holds no secret, and a test fails if one
  (or a personal address) is ever written into a file. List the names you want to keep out of the repository in
  `PRIVATE_WORDS` in your `.env`: the same test then fails if one of them shows up in a public file.
- Everything printed or saved goes through `redact`, so a key cannot appear in the terminal or in the CSV.
- `output/` holds names and job titles of real people: keep it private and delete it when you are done.
- Drafts are addressed to yourself. Real prospecting by email needs a proper sending tool, opt-out and a legal check.

## Known limits

- Retail proof comes from web snippets and chain sites, so a brand sold only in unindexed shops scores lower, not
  zero. A source can be weak (a marketplace page, a social post): the check proves that a retailer's name is written
  there, not that the page proves the sale.
- Generic company names (a restaurant and a food brand can share one) need the website address in the CSV. The program
  reads the "official" website, which for a large group is not always the one that talks about distribution.
- Apollo's free search hides last names and locations. Title lists are English and are guesses about who buys.
- Weights in the score are first guesses, not tuned on real replies. Nothing here measures accuracy yet: that needs a
  hand-labelled sample.
- The country bonus only rewards the UK, the market this demo is aimed at.