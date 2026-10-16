"""Check invoices from the command line.

    python -m harness data/samples/03_halvorsen_price_up.pdf [more.pdf ...]
    python -m harness --extractor baseline data/samples/*.pdf
"""
import argparse
import json

from .extract import make_extractor
from .pipeline import check_invoice
from .reference import Reference

ICON = {"approve": "✓ READY", "review": "? REVIEW", "hold": "✕ HOLD"}

ap = argparse.ArgumentParser(prog="python -m harness")
ap.add_argument("pdfs", nargs="+")
ap.add_argument("--extractor", choices=["llm", "baseline"])
ap.add_argument("--json", action="store_true", help="print the full result as JSON")
args = ap.parse_args()

extractor = make_extractor(args.extractor)
ref = Reference.load()
for path in args.pdfs:
    r = check_invoice(path, extractor, ref)
    if args.json:
        print(json.dumps(r.model_dump(), indent=2))
        continue
    print(f"{ICON[r.decision]:9} {r.source_file}: {r.headline}")
    for i in r.issues:
        print(f"          - [{i.severity}] {i.title}: {i.detail}")
