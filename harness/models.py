"""Data shapes shared by every stage of the harness.

Kept deliberately small: the extraction schema is what the model must fill,
everything else is produced by deterministic code.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# What we pull off the invoice (the only thing the LLM produces)
# ---------------------------------------------------------------------------
class LineItem(BaseModel):
    description: str
    sku: Optional[str] = None
    quantity: float
    unit_price: float
    line_total: Optional[float] = None


class InvoiceExtraction(BaseModel):
    supplier_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD")
    po_number: Optional[str] = None
    currency: Optional[str] = "USD"
    lines: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None


# ---------------------------------------------------------------------------
# Reference data (what the business already knows)
# ---------------------------------------------------------------------------
class POLine(BaseModel):
    line: int
    sku: str
    description: str
    qty_ordered: float
    qty_received: float
    unit_price: float


class PurchaseOrder(BaseModel):
    po_number: str
    supplier_id: str
    supplier_name: str
    order_date: str
    tax_rate: float
    lines: list[POLine]


class PaidInvoice(BaseModel):
    supplier_id: str
    invoice_number: str
    invoice_date: str
    po_number: str
    total: float
    lines: list[dict]  # [{"sku": str, "quantity": float}]


# ---------------------------------------------------------------------------
# What the harness concludes
# ---------------------------------------------------------------------------
Severity = Literal["block", "review", "info"]
Decision = Literal["approve", "review", "hold"]


class Issue(BaseModel):
    code: str
    severity: Severity
    title: str
    detail: str
    next_step: str = ""
    line_index: Optional[int] = None  # index into extraction.lines
    amount_at_stake: Optional[float] = None


class LineMatch(BaseModel):
    invoice_index: int
    po_line: Optional[int] = None
    method: Literal["sku", "description", "none"]
    score: float = 0.0


class CheckResult(BaseModel):
    source_file: str
    extractor: str
    extraction: Optional[InvoiceExtraction] = None
    ungrounded_fields: list[str] = Field(default_factory=list)
    supplier_id: Optional[str] = None
    po_number_matched: Optional[str] = None
    line_matches: list[LineMatch] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    decision: Decision = "review"
    headline: str = ""
    amount_at_stake: float = 0.0
    seconds: float = 0.0
