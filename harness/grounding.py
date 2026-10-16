"""Stage 2: is every extracted value actually printed on the invoice?

The cheapest hallucination guard there is: a value the model "read" that does not
appear anywhere in the source text is not trusted, and the clerk is told which field
to double-check. It catches invented PO numbers and mis-transcribed prices; it does
not catch a real number copied into the wrong field (math + matching checks do that).
"""
from __future__ import annotations

import datetime as dt
import re

from .models import InvoiceExtraction
from .text import alnum


def _number_forms(v: float) -> set[str]:
    forms = {f"{v:,.2f}", f"{v:.2f}"}
    if float(v).is_integer():
        forms |= {str(int(v)), f"{int(v):,}"}
    else:
        forms |= {f"{v:g}"}
    return forms


def _date_forms(iso: str) -> set[str]:
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return {iso}
    return {iso, d.strftime("%B %-d, %Y"), d.strftime("%b %-d, %Y"), d.strftime("%m/%d/%Y"),
            d.strftime("%-m/%-d/%Y"), d.strftime("%d %B %Y"), d.strftime("%B %d, %Y")}


def ungrounded_fields(ex: InvoiceExtraction, text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    squashed = alnum(text)
    plain = flat.replace("$", "")

    def has_str(s: str | None) -> bool:
        return s is None or (alnum(s) != "" and alnum(s) in squashed)

    def has_num(v: float | None) -> bool:
        if v is None:
            return True
        return any(re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d]|\.\d)", plain) for f in _number_forms(v))

    missing = []
    if not has_str(ex.invoice_number):
        missing.append("invoice_number")
    if not has_str(ex.po_number):
        missing.append("po_number")
    if ex.invoice_date and not any(f.lower() in flat.lower() for f in _date_forms(ex.invoice_date)):
        missing.append("invoice_date")
    for name in ("subtotal", "tax", "total"):
        if not has_num(getattr(ex, name)):
            missing.append(name)
    for i, ln in enumerate(ex.lines):
        if ln.sku and not has_str(ln.sku):
            missing.append(f"lines[{i}].sku")
        for name in ("quantity", "unit_price", "line_total"):
            if not has_num(getattr(ln, name)):
                missing.append(f"lines[{i}].{name}")
    return missing
