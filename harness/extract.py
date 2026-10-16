"""Stage 1: turn a PDF into an InvoiceExtraction.

Two interchangeable extractors:
  * LLMExtractor      - Claude reads the PDF's text layer and fills a strict schema
                        via a forced tool call. This is the only place a model is used.
  * BaselineExtractor - regexes tuned for "normal" table invoices. It exists so the
                        app and evals run without a key, and as a yardstick for how much
                        the model actually earns its place (see evals/).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber
import requests
from pydantic import ValidationError

from . import config
from .models import InvoiceExtraction, LineItem


class UnreadablePDF(Exception):
    """The PDF has no usable text layer (scan, fax, photo)."""


class ExtractionFailed(Exception):
    """The extractor could not produce a valid record."""


def pdf_text(path: str | Path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages[:5]]
    text = "\n".join(pages).strip()
    if len(re.sub(r"\s", "", text)) < 40:
        raise UnreadablePDF("No readable text in this PDF - it looks like a scan or a photo.")
    return text


# ---------------------------------------------------------------------------
# LLM extractor
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You transcribe supplier invoices for an accounts-payable team.

Rules:
- Copy values exactly as printed. Never calculate, correct, or infer a number.
  If the invoice's own arithmetic looks wrong, transcribe it wrong - a later step checks it.
- If a field is not on the invoice, use null. Do not guess a PO number.
- po_number: the buyer's purchase-order reference, in whatever format the supplier printed
  (e.g. "PO-45000110", "4500-0120", "your order 45000131"). Not the invoice number.
- invoice_date: convert to YYYY-MM-DD.
- lines: one entry per billed line, including fees and surcharges. sku is the supplier's
  item code if one is printed for that line, else null. quantity and unit_price as numbers.
- subtotal / tax / total: as printed (the total is the amount the supplier asks to be paid).
Call record_invoice exactly once."""

_NUM = {"type": "number"}
_NUM_NULL = {"type": ["number", "null"]}
_STR_NULL = {"type": ["string", "null"]}

INVOICE_TOOL = {
    "name": "record_invoice",
    "description": "Record the fields transcribed from one supplier invoice.",
    "input_schema": {
        "type": "object",
        "properties": {
            "supplier_name": _STR_NULL,
            "invoice_number": _STR_NULL,
            "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "po_number": _STR_NULL,
            "currency": _STR_NULL,
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "sku": _STR_NULL,
                        "quantity": _NUM,
                        "unit_price": _NUM,
                        "line_total": _NUM_NULL,
                    },
                    "required": ["description", "sku", "quantity", "unit_price", "line_total"],
                },
            },
            "subtotal": _NUM_NULL,
            "tax": _NUM_NULL,
            "total": _NUM_NULL,
        },
        "required": ["supplier_name", "invoice_number", "invoice_date", "po_number",
                     "lines", "subtotal", "tax", "total"],
    },
}


class LLMExtractor:
    name = "llm"

    def __init__(self, api_key: str | None = None, model: str | None = None, session=None):
        self.api_key = api_key or config.ANTHROPIC_API_KEY
        self.model = model or config.ANTHROPIC_MODEL
        self.http = session or requests.Session()
        if not self.api_key:
            raise ExtractionFailed("ANTHROPIC_API_KEY is not set.")

    def _call(self, messages: list[dict]) -> dict:
        resp = self.http.post(
            config.ANTHROPIC_URL,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 2048, "temperature": 0,
                  "system": SYSTEM_PROMPT, "tools": [INVOICE_TOOL],
                  "tool_choice": {"type": "tool", "name": "record_invoice"},
                  "messages": messages},
            timeout=config.LLM_TIMEOUT_S,
        )
        if resp.status_code >= 400:
            raise ExtractionFailed(f"Model API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    @staticmethod
    def _tool_input(body: dict) -> tuple[dict, str]:
        for block in body.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "record_invoice":
                return block["input"], block["id"]
        raise ExtractionFailed("Model did not return a record_invoice call.")

    def extract(self, text: str) -> InvoiceExtraction:
        messages = [{"role": "user", "content": f"<invoice_text>\n{text}\n</invoice_text>"}]
        body = self._call(messages)
        data, tool_id = self._tool_input(body)
        try:
            return InvoiceExtraction.model_validate(data)
        except ValidationError as err:
            # One repair attempt: show the model exactly what failed.
            messages += [
                {"role": "assistant", "content": body["content"]},
                {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": tool_id, "is_error": True,
                    "content": f"Schema validation failed, call record_invoice again:\n{err}"}]},
            ]
            data, _ = self._tool_input(self._call(messages))
            try:
                return InvoiceExtraction.model_validate(data)
            except ValidationError as err2:
                raise ExtractionFailed(f"Model output failed validation twice: {err2}") from err2


# ---------------------------------------------------------------------------
# Regex baseline
# ---------------------------------------------------------------------------
_MONEY = r"\$?\s?([\d,]+\.\d{2})"


def _num(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


def _find(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text, re.I | re.M)
        if m:
            return m.group(1).strip()
    return None


class BaselineExtractor:
    name = "baseline"

    LINE = re.compile(
        rf"^(?P<sku>[A-Z]{{1,4}}-[A-Z0-9]+)\s+(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)(?:\s*(?:ea|pcs|units?))?"
        rf"\s+{_MONEY}\s+{_MONEY}\s*$", re.M)

    def extract(self, text: str) -> InvoiceExtraction:
        lines = [LineItem(sku=m["sku"], description=m["desc"], quantity=float(m["qty"]),
                          unit_price=_num(m.group(4)), line_total=_num(m.group(5)))
                 for m in self.LINE.finditer(text)]
        date = _find([r"invoice date:?\s*(\d{4}-\d{2}-\d{2})", r"\bdate:?\s*(\d{4}-\d{2}-\d{2})"], text)
        first = next((ln.strip() for ln in text.splitlines() if ln.strip() and ln.strip() != "INVOICE"), None)
        if not lines:
            raise ExtractionFailed("Baseline reader found no line-item table.")
        return InvoiceExtraction(
            supplier_name=first,
            invoice_number=_find([r"invoice\s*(?:no\.?|number|#)\s*:?\s*([A-Z0-9][A-Z0-9-]+)"], text),
            invoice_date=date,
            po_number=_find([r"P\.?O\.?\s*(?:number|no\.?|#)\s*:?\s*([A-Z0-9][A-Z0-9-]+)"], text),
            lines=lines,
            subtotal=_num(_find([rf"subtotal\s*{_MONEY}"], text)),
            tax=_num(_find([rf"tax[^\n$\d]*(?:\(\d+%\))?\s*{_MONEY}"], text)),
            total=_num(_find([rf"total due\s*{_MONEY}", rf"amount due\s*{_MONEY}", rf"^total\s*{_MONEY}"], text)),
        )


class OracleExtractor:
    """Returns the ground-truth record. Only used by evals to test the checks in isolation."""
    name = "oracle"

    def __init__(self, truth_by_file: dict[str, dict]):
        self.truth = truth_by_file
        self.current_file: str | None = None

    def extract(self, text: str) -> InvoiceExtraction:
        return InvoiceExtraction.model_validate(self.truth[self.current_file]["extraction"])


def make_extractor(kind: str | None = None):
    kind = kind or config.EXTRACTOR
    if kind == "llm":
        return LLMExtractor()
    if kind == "baseline":
        return BaselineExtractor()
    raise ValueError(f"Unknown extractor {kind!r}")


if __name__ == "__main__":  # quick manual check: python -m harness.extract file.pdf
    import sys
    t = pdf_text(sys.argv[1])
    print(json.dumps(make_extractor().extract(t).model_dump(), indent=2))
