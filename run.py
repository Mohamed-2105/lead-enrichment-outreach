"""Entry point.

    python3 run.py                         # the whole list in data/leads_input.csv
    python3 run.py --max-leads 4           # a test on the first four companies
    python3 run.py --input my.csv --output out.csv
"""

import argparse
import sys

from leadrouter import settings
from leadrouter.log import log
from leadrouter.pipeline import run_pipeline


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Enrich a list of brands, score them, find a contact, draft an email.")
    parser.add_argument("--input", help="CSV with at least a company_name column")
    parser.add_argument("--output", help="where to write the result CSV")
    parser.add_argument("--max-leads", type=int, help="only process the first N companies (a test run)")
    args = parser.parse_args(argv)

    if args.input:
        settings.INPUT_CSV = args.input
    if args.output:
        settings.OUTPUT_CSV = args.output
    if args.max_leads is not None:
        settings.MAX_LEADS = args.max_leads

    if not settings.GEMINI_API_KEY:
        log("GEMINI_API_KEY is missing. Copy .env.example to .env and fill it in.")
        return 1
    for service, ok in settings.configured_services().items():
        log(f"  {service}: {'configured' if ok else 'not configured'}")
    run_pipeline()
    return 0


if __name__ == "__main__":
    sys.exit(main())
