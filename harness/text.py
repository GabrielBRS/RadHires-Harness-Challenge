"""Small normalisation helpers used by matching and grounding."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_LEGAL = {"llc", "inc", "co", "company", "corp", "corporation", "ltd", "the", "and"}


def alnum(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def tokens(s: str | None) -> list[str]:
    s = (s or "").lower().replace("&", " ")
    s = re.sub(r"(\d)\s*(in|inch|\")\b", r"\1in", s)       # 3 inch / 3" -> 3in
    s = re.sub(r"box\s*(/|of)\s*", "box ", s)                # box/350, box of 350 -> box 350
    return [t for t in re.split(r"[^a-z0-9/.#-]+", s) if t]


def similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity for short product descriptions. Token overlap + char ratio."""
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb) / len(ta | tb)
    chars = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(overlap, chars), 3)


def supplier_similarity(a: str | None, b: str | None) -> float:
    ta = {t for t in tokens(a) if t not in _LEGAL}
    tb = {t for t in tokens(b) if t not in _LEGAL}
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    contain = shared / min(len(ta), len(tb))   # "Ridgeline Lumber" inside "Ridgeline Lumber & Millwork"
    jaccard = shared / len(ta | tb)
    return round(0.5 * contain + 0.5 * jaccard, 3)


def po_key(s: str | None) -> str:
    """'PO-45000120', '4500-0120', 'PO# 4500 0120' -> '45000120'."""
    digits = re.sub(r"\D", "", s or "")
    return digits.lstrip("0")


def money(v: float | None) -> str:
    return "—" if v is None else f"${v:,.2f}"


def qty(v: float | None) -> str:
    if v is None:
        return "—"
    return str(int(v)) if float(v).is_integer() else f"{v:g}"
