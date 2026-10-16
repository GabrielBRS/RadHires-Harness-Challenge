"""The harness: PDF in, a decision the clerk can act on out.

    read text ─► extract (LLM) ─► ground every value ─► arithmetic
        ─► find PO + supplier ─► duplicates ─► match lines ─► price / qty
        ─► decide (approve | review | hold) + plain-language reasons

Anything that goes wrong becomes an Issue the clerk can see; nothing fails silently
and nothing is auto-paid. "approve" means "ready for you to approve", not "paid".
"""
from __future__ import annotations

import time
from pathlib import Path

from . import checks, config
from .grounding import ungrounded_fields
from .extract import ExtractionFailed, UnreadablePDF, pdf_text
from .models import CheckResult, Issue
from .reference import Reference
from .text import money, supplier_similarity


def decide(result: CheckResult) -> None:
    sev = {i.severity for i in result.issues}
    result.amount_at_stake = round(sum(abs(i.amount_at_stake or 0) for i in result.issues
                                       if i.severity == "review"), 2)
    if "block" in sev:
        result.decision = "hold"
        first = next(i for i in result.issues if i.severity == "block")
        result.headline = f"{first.title}."
    elif "review" in sev:
        result.decision = "review"
        flagged = [i for i in result.issues if i.severity == "review"]
        more = f" (+{len(flagged) - 1} more)" if len(flagged) > 1 else ""
        stake = f" · {money(result.amount_at_stake)} in question" if result.amount_at_stake else ""
        result.headline = f"{flagged[0].title}{more}{stake}."
    else:
        result.decision = "approve"
        total = money(result.extraction.total) if result.extraction else ""
        result.headline = (f"Matches {result.po_number_matched} and what the warehouse received. "
                           f"OK to pay {total}.")


def check_invoice(path: str | Path, extractor, ref: Reference) -> CheckResult:
    t0 = time.time()
    result = CheckResult(source_file=Path(path).name, extractor=extractor.name)
    try:
        text = pdf_text(path)
    except UnreadablePDF as err:
        result.issues.append(Issue(code="UNREADABLE", severity="review", title="Couldn't read this PDF",
                                   detail=str(err),
                                   next_step="Ask the supplier for a digital (not scanned) PDF, or key it in manually."))
        return _finish(result, t0)

    try:
        ex = extractor.extract(text)
    except ExtractionFailed as err:
        result.issues.append(Issue(code="EXTRACTION_FAILED", severity="review",
                                   title="Couldn't make sense of this invoice", detail=str(err),
                                   next_step="Key this one in manually."))
        return _finish(result, t0)
    result.extraction = ex

    # 1. Trust: values not printed on the page are flagged, and an unprinted PO is not used.
    result.ungrounded_fields = ungrounded_fields(ex, text)
    result.issues += checks.grounding_issue(result.ungrounded_fields, ex)
    po_printed = None if "po_number" in result.ungrounded_fields else ex.po_number

    result.issues += checks.required_fields(ex)
    result.issues += checks.arithmetic(ex)

    # 2. Who is this from, and which PO is it for?
    po = ref.po(po_printed)
    if po:
        score = supplier_similarity(ex.supplier_name, po.supplier_name)
        result.issues += checks.supplier_check(ex, po, score)
        result.supplier_id = po.supplier_id if score >= config.SUPPLIER_MATCH_MIN else ref.supplier_for_name(ex.supplier_name)[0]
        result.po_number_matched = po.po_number
    else:
        result.supplier_id = ref.supplier_for_name(ex.supplier_name)[0]
        result.issues += checks.po_not_found(ex, result.supplier_id, ref)

    # 3. Have we seen it before?
    dup = checks.duplicates(ex, result.supplier_id, ref)
    result.issues += dup
    exact_duplicate = any(i.code == "DUPLICATE_INVOICE" for i in dup)

    # 4. Line-by-line three-way match.
    if po and not any(i.code == "SUPPLIER_MISMATCH" for i in result.issues):
        result.line_matches = checks.match_lines(ex, po)
        result.issues += checks.line_checks(ex, po, result.line_matches, ref, skip_history=exact_duplicate)
    return _finish(result, t0)


def _finish(result: CheckResult, t0: float) -> CheckResult:
    order = {"block": 0, "review": 1, "info": 2}
    result.issues.sort(key=lambda i: order[i.severity])
    decide(result)
    result.seconds = round(time.time() - t0, 2)
    return result

