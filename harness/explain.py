"""Supplier query email, built from the issues.

A template on purpose, not an LLM: this goes to an outside company and must only
contain facts the checks established. The clerk can edit it before sending.
"""
from __future__ import annotations

from .models import CheckResult
from .text import money

SUPPLIER_FACING = {"PRICE_VARIANCE", "QTY_EXCEEDS_RECEIVED", "ITEM_NOT_ON_PO", "MATH_ERROR",
                   "PO_NOT_FOUND", "POSSIBLE_DUPLICATE", "DUPLICATE_INVOICE", "UNREADABLE"}

ASK = {
    "PRICE_VARIANCE": "a credit note for the price difference, or confirmation of who agreed the new price",
    "QTY_EXCEEDS_RECEIVED": "a corrected invoice for the quantity delivered so far",
    "ITEM_NOT_ON_PO": "confirmation of who approved the additional charge",
    "MATH_ERROR": "a corrected invoice",
    "PO_NOT_FOUND": "the purchase order number this invoice relates to",
    "POSSIBLE_DUPLICATE": "confirmation that this is a separate delivery from the earlier invoice",
    "DUPLICATE_INVOICE": "confirmation of what is still outstanding, as our records show this invoice as paid",
    "UNREADABLE": "a digital PDF copy of the invoice (the copy we received is a scan)",
}


def supplier_email(r: CheckResult) -> dict | None:
    issues = [i for i in r.issues if i.code in SUPPLIER_FACING]
    if not issues:
        return None
    ex = r.extraction
    number = ex.invoice_number if ex and ex.invoice_number else "(no number)"
    greeting = f"Hello {ex.supplier_name} team," if ex and ex.supplier_name else "Hello,"
    po = f" (our PO {r.po_number_matched})" if r.po_number_matched else ""
    points = "\n".join(f"  • {i.detail}" for i in issues)
    asks = []
    for i in issues:
        if ASK[i.code] not in asks:
            asks.append(ASK[i.code])
    total = f" for {money(ex.total)}" if ex and ex.total else ""
    what = "one thing" if len(issues) == 1 else "a few things"
    body = (
        f"{greeting}\n\n"
        f"We're reviewing your invoice {number}{total}{po} and need to check {what} before we can pay it:\n\n"
        f"{points}\n\n"
        f"Could you please send us {'; and '.join(asks)}?\n\n"
        f"We'll process payment as soon as this is sorted. Thank you,\n\n"
        f"Accounts Payable\nLumen Supply Co.\n"
    )
    return {"subject": f"Query on invoice {number}{po}", "body": body}
