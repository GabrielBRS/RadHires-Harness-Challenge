"""Stage 3: deterministic AP checks. No model calls in here on purpose:
every flag must be explainable to an auditor with a number and a rule."""
from __future__ import annotations

import datetime as dt

from . import config
from .models import InvoiceExtraction, Issue, LineMatch, PurchaseOrder
from .reference import Reference
from .text import alnum, money, qty, similarity

FIELD_NAMES = {
    "invoice_number": "invoice number", "po_number": "PO number", "invoice_date": "invoice date",
    "subtotal": "subtotal", "tax": "tax", "total": "total",
    "sku": "item code", "quantity": "quantity", "unit_price": "unit price", "line_total": "line amount",
}


def describe_field(path: str, ex: InvoiceExtraction) -> str:
    if path.startswith("lines["):
        idx = int(path[6:path.index("]")])
        what = FIELD_NAMES.get(path.split(".")[-1], path)
        desc = ex.lines[idx].description if idx < len(ex.lines) else f"line {idx + 1}"
        return f"{what} on “{desc}”"
    return FIELD_NAMES.get(path, path)


# ---------------------------------------------------------------------------
def grounding_issue(missing: list[str], ex: InvoiceExtraction) -> list[Issue]:
    if not missing:
        return []
    names = ", ".join(describe_field(m, ex) for m in missing[:5])
    more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
    return [Issue(code="UNCONFIRMED_FIELDS", severity="review",
                  title="Some values couldn't be confirmed on the page",
                  detail=f"I read these but couldn't find them printed on the invoice: {names}{more}.",
                  next_step="Compare the highlighted fields with the PDF before approving.")]


def required_fields(ex: InvoiceExtraction) -> list[Issue]:
    missing = [n for n, v in [("invoice number", ex.invoice_number), ("total", ex.total)] if v is None]
    if not ex.lines:
        missing.append("line items")
    if not missing:
        return []
    return [Issue(code="MISSING_FIELD", severity="review",
                  title="Invoice is missing basic information",
                  detail=f"Couldn't find: {', '.join(missing)}.",
                  next_step="Check the PDF; if it's really missing, ask the supplier for a complete invoice.")]


def arithmetic(ex: InvoiceExtraction) -> list[Issue]:
    issues = []
    tol = config.MATH_TOLERANCE
    for i, ln in enumerate(ex.lines):
        if ln.line_total is None:
            continue
        expected = round(ln.quantity * ln.unit_price, 2)
        if abs(expected - ln.line_total) > tol:
            issues.append(Issue(
                code="MATH_ERROR", severity="review", line_index=i,
                amount_at_stake=round(ln.line_total - expected, 2),
                title="Supplier's arithmetic doesn't add up",
                detail=f"“{ln.description}”: {qty(ln.quantity)} × {money(ln.unit_price)} = {money(expected)}, "
                       f"but the invoice says {money(ln.line_total)}.",
                next_step="Ask the supplier for a corrected invoice."))
    line_sum = round(sum((l.line_total if l.line_total is not None else l.quantity * l.unit_price)
                         for l in ex.lines), 2)
    if ex.subtotal is not None and abs(line_sum - ex.subtotal) > tol:
        issues.append(Issue(
            code="MATH_ERROR", severity="review", amount_at_stake=round(ex.subtotal - line_sum, 2),
            title="Lines don't add up to the subtotal",
            detail=f"The lines add up to {money(line_sum)}; the subtotal says {money(ex.subtotal)}.",
            next_step="Ask the supplier for a corrected invoice."))
    if None not in (ex.subtotal, ex.tax, ex.total) and abs(ex.subtotal + ex.tax - ex.total) > tol:
        issues.append(Issue(
            code="MATH_ERROR", severity="review", amount_at_stake=round(ex.total - ex.subtotal - ex.tax, 2),
            title="Total doesn't match subtotal plus tax",
            detail=f"{money(ex.subtotal)} + {money(ex.tax)} tax = {money(ex.subtotal + ex.tax)}, "
                   f"but the total due says {money(ex.total)}.",
            next_step="Ask the supplier for a corrected invoice."))
    return issues


# ---------------------------------------------------------------------------
def _days_apart(a: str | None, b: str | None) -> int | None:
    try:
        return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def duplicates(ex: InvoiceExtraction, supplier_id: str | None, ref: Reference) -> list[Issue]:
    if not supplier_id:
        return []
    num = alnum(ex.invoice_number)
    seen = [dict(src="paid", supplier_id=p.supplier_id, invoice_number=p.invoice_number,
                 invoice_date=p.invoice_date, total=p.total) for p in ref.paid]
    seen += [dict(src="queue", **q) for q in ref.in_queue]
    for s in seen:
        if s["supplier_id"] != supplier_id:
            continue
        where = (f"already paid on {s['invoice_date']}" if s["src"] == "paid"
                 else "already in your queue")
        if num and alnum(s["invoice_number"]) == num:
            return [Issue(code="DUPLICATE_INVOICE", severity="block", amount_at_stake=ex.total,
                          title="This invoice was already processed",
                          detail=f"Invoice {s['invoice_number']} from this supplier was {where}.",
                          next_step="Don't pay. If the supplier says it's still unpaid, check the payment run first.")]
    for s in seen:
        if s["supplier_id"] != supplier_id or ex.total is None:
            continue
        days = _days_apart(ex.invoice_date, s["invoice_date"])
        if abs(s["total"] - ex.total) < 0.01 and days is not None and days <= config.DUPLICATE_WINDOW_DAYS:
            where = (f"was paid on {s['invoice_date']}" if s["src"] == "paid" else "is already in your queue")
            return [Issue(code="POSSIBLE_DUPLICATE", severity="block", amount_at_stake=ex.total,
                          title="Looks like a re-issue of an invoice we already have",
                          detail=f"Same supplier and same amount ({money(ex.total)}) as invoice "
                                 f"{s['invoice_number']}, which {where}. Dated {days} days apart, "
                                 f"under a different invoice number.",
                          next_step="Confirm with the supplier that this is a separate delivery before paying.")]
    return []


def supplier_check(ex: InvoiceExtraction, po: PurchaseOrder, score: float) -> list[Issue]:
    if score >= config.SUPPLIER_MATCH_MIN:
        return []
    return [Issue(code="SUPPLIER_MISMATCH", severity="block",
                  title="PO belongs to a different supplier",
                  detail=f"The invoice is from “{ex.supplier_name}”, but {po.po_number} was raised with "
                         f"{po.supplier_name}.",
                  next_step="Don't pay. Check whether the PO number was mistyped.")]


def po_not_found(ex: InvoiceExtraction, supplier_id: str | None, ref: Reference) -> list[Issue]:
    candidates = [p for p in ref.pos if p.supplier_id == supplier_id]
    skus = {alnum(l.sku) for l in ex.lines if l.sku}
    candidates.sort(key=lambda p: -len(skus & {alnum(l.sku) for l in p.lines}))
    hint = ""
    if candidates and skus & {alnum(l.sku) for l in candidates[0].lines}:
        hint = f" The items look like {candidates[0].po_number} ({candidates[0].order_date})."
    printed = f"“{ex.po_number}”" if ex.po_number else "no PO number"
    return [Issue(code="PO_NOT_FOUND", severity="block", amount_at_stake=ex.total,
                  title="No matching purchase order",
                  detail=f"The invoice shows {printed}, and I couldn't match it to an open PO.{hint}",
                  next_step="Ask the supplier which PO this is for, or check with whoever placed the order.")]


# ---------------------------------------------------------------------------
def match_lines(ex: InvoiceExtraction, po: PurchaseOrder) -> list[LineMatch]:
    """Item code first (exact), then best description match among the PO lines still free."""
    free = {l.line: l for l in po.lines}
    matches: dict[int, LineMatch] = {}
    for i, ln in enumerate(ex.lines):
        hit = next((l for l in free.values() if ln.sku and alnum(l.sku) == alnum(ln.sku)), None)
        if hit:
            matches[i] = LineMatch(invoice_index=i, po_line=hit.line, method="sku", score=1.0)
            free.pop(hit.line)
    pairs = sorted(((similarity(ln.description, pl.description), i, pl.line)
                    for i, ln in enumerate(ex.lines) if i not in matches
                    for pl in free.values()), reverse=True)
    for score, i, line in pairs:
        if i in matches or line not in free or score < config.DESCRIPTION_MATCH_MIN:
            continue
        matches[i] = LineMatch(invoice_index=i, po_line=line, method="description", score=score)
        free.pop(line)
    return [matches.get(i, LineMatch(invoice_index=i, method="none")) for i in range(len(ex.lines))]


def line_checks(ex: InvoiceExtraction, po: PurchaseOrder, matches: list[LineMatch],
                ref: Reference, skip_history: bool) -> list[Issue]:
    issues = []
    by_line = {l.line: l for l in po.lines}
    for m in matches:
        ln = ex.lines[m.invoice_index]
        if m.po_line is None:
            issues.append(Issue(
                code="ITEM_NOT_ON_PO", severity="review", line_index=m.invoice_index,
                amount_at_stake=ln.line_total if ln.line_total is not None else round(ln.quantity * ln.unit_price, 2),
                title="Charge that isn't on the PO",
                detail=f"“{ln.description}” ({money(ln.line_total)}) isn't on {po.po_number}.",
                next_step="Check with whoever placed the order whether this charge was agreed."))
            continue
        pl = by_line[m.po_line]
        diff = round(ln.unit_price - pl.unit_price, 4)
        limit = max(config.PRICE_TOLERANCE_ABS, pl.unit_price * config.PRICE_TOLERANCE_PCT / 100)
        if diff > limit:
            issues.append(Issue(
                code="PRICE_VARIANCE", severity="review", line_index=m.invoice_index,
                amount_at_stake=round(diff * ln.quantity, 2),
                title="Charged more than the agreed price",
                detail=f"“{pl.description}”: billed {money(ln.unit_price)} each, the PO says "
                       f"{money(pl.unit_price)}. That's {money(diff * ln.quantity)} extra on {qty(ln.quantity)}.",
                next_step="Ask the supplier for a credit, or confirm the new price with purchasing."))
        elif diff < -limit:
            issues.append(Issue(
                code="PRICE_BELOW_PO", severity="info", line_index=m.invoice_index,
                title="Billed below the PO price",
                detail=f"“{pl.description}”: billed {money(ln.unit_price)}, PO says {money(pl.unit_price)}. "
                       f"In our favour.",
                next_step="No action needed."))
        billed_before = 0.0 if skip_history else ref.previously_billed(po.po_number, pl.sku)
        billable = pl.qty_received - billed_before
        if ln.quantity > billable + 1e-9:
            excess = ln.quantity - max(billable, 0)
            if billed_before:
                detail = (f"“{pl.description}”: billed {qty(ln.quantity)}. The warehouse received "
                          f"{qty(pl.qty_received)} and we've already paid for {qty(billed_before)}, so only "
                          f"{qty(max(billable, 0))} were left to bill. {qty(excess)} over "
                          f"({money(excess * ln.unit_price)}).")
                step = "Check whether this repeats an earlier invoice before paying."
            else:
                detail = (f"“{pl.description}”: billed {qty(ln.quantity)}, the warehouse received "
                          f"{qty(pl.qty_received)}. {qty(excess)} not received yet "
                          f"({money(excess * ln.unit_price)}).")
                step = "Check with the warehouse; if it hasn't arrived, ask the supplier to bill it on delivery."
            issues.append(Issue(
                code="QTY_EXCEEDS_RECEIVED", severity="review", line_index=m.invoice_index,
                amount_at_stake=round(excess * ln.unit_price, 2),
                title="Billed for more than we received", detail=detail, next_step=step))
    return issues
