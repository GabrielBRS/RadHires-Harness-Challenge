"""Score the harness against the 17 labelled sample invoices.

    python evals/run_eval.py --extractor oracle     # checks only (perfect extraction)
    python evals/run_eval.py --extractor baseline   # regex reader, no key needed
    python evals/run_eval.py --extractor llm        # Claude (needs ANTHROPIC_API_KEY)
    python evals/run_eval.py --extractor replay     # re-score saved LLM extractions, no API calls

Splitting by extractor tells us *where* errors come from: if oracle is perfect and llm
isn't, the problem is reading, not the business rules.

The number that matters most is UNSAFE APPROVALS: invoices the harness marked "ready
to approve" that actually had a problem. That's money out of the door. A wrongly
flagged clean invoice only costs the clerk a minute.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import config  # noqa: E402
from harness.extract import BaselineExtractor, LLMExtractor, OracleExtractor  # noqa: E402
from harness.models import InvoiceExtraction  # noqa: E402
from harness.pipeline import check_invoice  # noqa: E402
from harness.reference import Reference  # noqa: E402
from harness.text import alnum, po_key  # noqa: E402

RESULTS = ROOT / "evals" / "results"


class RecordingExtractor:
    """Wraps the LLM extractor and saves what it returned, so runs can be re-scored for free."""
    name = "llm"

    def __init__(self, inner):
        self.inner, self.saved, self.current_file = inner, {}, None

    def extract(self, text):
        ex = self.inner.extract(text)
        self.saved[self.current_file] = ex.model_dump()
        return ex


class ReplayExtractor:
    name = "replay"

    def __init__(self, saved: dict):
        self.saved, self.current_file = saved, None

    def extract(self, text):
        from harness.extract import ExtractionFailed
        if self.current_file not in self.saved:
            raise ExtractionFailed("no saved extraction for this file")
        return InvoiceExtraction.model_validate(self.saved[self.current_file])


def field_accuracy(pred: InvoiceExtraction | None, truth: dict) -> tuple[int, int]:
    """(correct, total) over header fields and line quantities/prices/amounts."""
    t = InvoiceExtraction.model_validate(truth)
    correct = total = 0

    def same(a, b):
        if isinstance(b, float) or isinstance(a, float):
            return a is not None and b is not None and abs(a - b) < 0.005
        if b is None:
            return a is None
        return a is not None and alnum(str(a)) == alnum(str(b))

    for f in ("invoice_number", "invoice_date", "subtotal", "tax", "total"):
        total += 1
        correct += bool(pred) and same(getattr(pred, f), getattr(t, f))
    total += 1
    correct += bool(pred) and po_key(pred.po_number) == po_key(t.po_number)
    remaining = list(pred.lines) if pred else []
    for tl in t.lines:
        total += 3
        best = next((pl for pl in remaining if same(pl.quantity, tl.quantity) and same(pl.unit_price, tl.unit_price)),
                    None)
        if best:
            remaining.remove(best)
            correct += 2 + same(best.line_total, tl.line_total)
    total += len(remaining)  # invented lines count against us
    return correct, total


def run(kind: str) -> dict:
    truth = json.loads((config.SAMPLES / "ground_truth.json").read_text())
    by_file = {t["file"]: t for t in truth}
    if kind == "oracle":
        ex = OracleExtractor(by_file)
    elif kind == "baseline":
        ex = BaselineExtractor()
    elif kind == "llm":
        ex = RecordingExtractor(LLMExtractor())
    elif kind == "replay":
        ex = ReplayExtractor(json.loads((RESULTS / "llm_extractions.json").read_text()))
    else:
        raise SystemExit(f"unknown extractor {kind}")

    rows, issue_tp, issue_fp, issue_fn = [], Counter(), Counter(), Counter()
    fields_ok = fields_all = 0
    for t in truth:
        ex.current_file = t["file"]
        r = check_invoice(config.SAMPLES / t["file"], ex, Reference.load())
        got = {i.code for i in r.issues if i.severity != "info"}
        want = set(t["expected_issues"])
        for c in got & want:
            issue_tp[c] += 1
        for c in got - want:
            issue_fp[c] += 1
        for c in want - got:
            issue_fn[c] += 1
        if t["layout"] != "SCAN":
            ok, n = field_accuracy(r.extraction, t["extraction"])
            fields_ok += ok
            fields_all += n
        rows.append(dict(file=t["file"], layout=t["layout"], want=t["expected_decision"], got=r.decision,
                         want_issues=sorted(want), got_issues=sorted(got), seconds=r.seconds,
                         unsafe=r.decision == "approve" and t["expected_decision"] != "approve",
                         headline=r.headline))
    if kind == "llm":
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "llm_extractions.json").write_text(json.dumps(ex.saved, indent=2))

    n = len(rows)
    codes = sorted(set(issue_tp) | set(issue_fp) | set(issue_fn))
    return dict(
        extractor=kind,
        model=config.ANTHROPIC_MODEL if kind in ("llm", "replay") else None,
        invoices=n,
        decision_accuracy=sum(r["want"] == r["got"] for r in rows) / n,
        unsafe_approvals=sum(r["unsafe"] for r in rows),
        needless_flags=sum(r["want"] == "approve" and r["got"] != "approve" for r in rows),
        field_accuracy=fields_ok / fields_all if fields_all else None,
        avg_seconds=round(sum(r["seconds"] for r in rows) / n, 2),
        issues={c: dict(tp=issue_tp[c], fp=issue_fp[c], fn=issue_fn[c]) for c in codes},
        rows=rows,
    )


def to_markdown(s: dict) -> str:
    out = [f"# Eval: `{s['extractor']}` extractor" + (f" ({s['model']})" if s["model"] else ""), "",
           "| metric | value |", "|---|---|",
           f"| invoices | {s['invoices']} |",
           f"| decision accuracy | {s['decision_accuracy']:.0%} |",
           f"| **unsafe approvals** (problem invoice marked ready) | **{s['unsafe_approvals']}** |",
           f"| needless flags (clean invoice sent to review/hold) | {s['needless_flags']} |",
           f"| extraction field accuracy | {s['field_accuracy']:.0%} |" if s["field_accuracy"] is not None else "",
           f"| avg seconds / invoice | {s['avg_seconds']} |", "",
           "## Issue detection", "", "| issue | caught | false alarm | missed |", "|---|---|---|---|"]
    out += [f"| {c} | {v['tp']} | {v['fp']} | {v['fn']} |" for c, v in s["issues"].items()]
    out += ["", "## Per invoice", "", "| file | layout | expected | got | issues expected | issues found |",
            "|---|---|---|---|---|---|"]
    for r in s["rows"]:
        mark = "" if r["want"] == r["got"] else (" ⚠️ UNSAFE" if r["unsafe"] else " ✗")
        out.append(f"| {r['file']} | {r['layout']} | {r['want']} | {r['got']}{mark} | "
                   f"{', '.join(r['want_issues']) or '—'} | {', '.join(r['got_issues']) or '—'} |")
    return "\n".join(line for line in out if line is not None) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", default="baseline", choices=["oracle", "baseline", "llm", "replay"])
    args = ap.parse_args()
    summary = run(args.extractor)
    RESULTS.mkdir(parents=True, exist_ok=True)
    md = to_markdown(summary)
    (RESULTS / f"{args.extractor}.md").write_text(md)
    (RESULTS / f"{args.extractor}.json").write_text(json.dumps(summary, indent=2))
    print(md)
    sys.exit(1 if summary["unsafe_approvals"] else 0)
