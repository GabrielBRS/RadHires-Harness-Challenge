"""Does the harness fail *closed* when the reader makes a mistake?

Takes every clean invoice, corrupts the (perfect) extraction the way an LLM or OCR
step plausibly would, and checks the invoice is no longer marked "ready to approve".
This tests the safety net independently of how good any particular model is.

    python evals/fault_injection.py
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import config  # noqa: E402
from harness.models import InvoiceExtraction  # noqa: E402
from harness.pipeline import check_invoice  # noqa: E402
from harness.reference import Reference  # noqa: E402


def price_misread(e):        # 18.50 read as 16.50, line total left as printed
    e["lines"][0]["unit_price"] = round(e["lines"][0]["unit_price"] - 2, 2)


def consistent_misread(e):   # model "fixes" the line so it looks self-consistent
    ln = e["lines"][0]
    ln["unit_price"] = round(ln["unit_price"] - 2, 2)
    ln["line_total"] = round(ln["unit_price"] * ln["quantity"], 2)
    e["subtotal"] = round(sum(l["line_total"] for l in e["lines"]), 2)
    e["tax"] = round(e["subtotal"] * 0.07, 2)
    e["total"] = round(e["subtotal"] + e["tax"], 2)


def qty_misread(e):
    e["lines"][0]["quantity"] += 1


def dropped_line(e):
    e["lines"] = e["lines"][1:] or e["lines"]
    if len(e["lines"]) == 1 and e["lines"][0]["quantity"] > 1:
        e["lines"][0]["quantity"] -= 1   # single-line invoice: drop part of it instead


def invented_line(e):
    e["lines"].append({"description": "Handling fee", "sku": None, "quantity": 1,
                       "unit_price": 12.0, "line_total": 12.0})


def hallucinated_po(e):      # a real PO number that is NOT printed on this invoice
    e["po_number"] = "PO-45000113" if e["po_number"] != "PO-45000113" else "PO-45000110"


def total_misread(e):
    e["total"] = round(e["total"] + 100, 2)


FAULTS = [price_misread, consistent_misread, qty_misread, dropped_line, invented_line,
          hallucinated_po, total_misread]


class Fixed:
    name = "fault"

    def __init__(self, data):
        self.data = data

    def extract(self, text):
        return InvoiceExtraction.model_validate(self.data)


def main() -> int:
    truth = json.loads((config.SAMPLES / "ground_truth.json").read_text())
    clean = [t for t in truth if t["expected_decision"] == "approve"]
    leaks, total = [], 0
    print(f"{'invoice':38} " + " ".join(f"{f.__name__[:12]:>12}" for f in FAULTS))
    for t in clean:
        cells = []
        for fault in FAULTS:
            data = copy.deepcopy(t["extraction"])
            fault(data)
            r = check_invoice(config.SAMPLES / t["file"], Fixed(data), Reference.load())
            total += 1
            if r.decision == "approve":
                leaks.append((t["file"], fault.__name__))
            cells.append(f"{r.decision:>12}")
        print(f"{t['file']:38} " + " ".join(cells))
    caught = total - len(leaks)
    print(f"\nFail-closed rate: {caught}/{total} corrupted extractions were NOT marked ready to approve.")
    for f, name in leaks:
        print(f"  LEAK: {name} on {f}")
    out = ROOT / "evals" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fault_injection.json").write_text(json.dumps(
        {"total": total, "caught": caught, "leaks": leaks}, indent=2))
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
