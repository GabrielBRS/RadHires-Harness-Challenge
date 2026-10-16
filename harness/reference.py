"""The business's own records. In real life: ERP purchase orders, goods receipts and the
payments ledger. Here: two JSON files (stubbed integration)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .models import PaidInvoice, PurchaseOrder
from .text import po_key, supplier_similarity


@dataclass
class Reference:
    pos: list[PurchaseOrder]
    paid: list[PaidInvoice]
    # invoices already in the clerk's queue this session: {"supplier_id","invoice_number","total","invoice_date","source"}
    in_queue: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, po_file: Path = config.PO_FILE, paid_file: Path = config.PAID_FILE) -> "Reference":
        pos = [PurchaseOrder.model_validate(p) for p in json.loads(Path(po_file).read_text())]
        paid = [PaidInvoice.model_validate(p) for p in json.loads(Path(paid_file).read_text())]
        return cls(pos, paid)

    def po(self, printed: str | None) -> PurchaseOrder | None:
        key = po_key(printed)
        if not key:
            return None
        return next((p for p in self.pos if po_key(p.po_number) == key), None)

    def suppliers(self) -> dict[str, str]:
        return {p.supplier_id: p.supplier_name for p in self.pos}

    def supplier_for_name(self, name: str | None) -> tuple[str | None, float]:
        best, score = None, 0.0
        for sid, sname in self.suppliers().items():
            s = supplier_similarity(name, sname)
            if s > score:
                best, score = sid, s
        return (best, score) if score >= config.SUPPLIER_MATCH_MIN else (None, score)

    def previously_billed(self, po_number: str, sku: str, exclude_invoice: str | None = None) -> float:
        total = 0.0
        for inv in self.paid:
            if inv.po_number != po_number or inv.invoice_number == exclude_invoice:
                continue
            total += sum(l["quantity"] for l in inv.lines if l["sku"] == sku)
        return total
