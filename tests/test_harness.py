"""Unit tests. Run: python -m unittest discover tests"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import config  # noqa: E402
from harness.checks import match_lines  # noqa: E402
from harness.extract import ExtractionFailed, LLMExtractor, pdf_text  # noqa: E402
from harness.grounding import ungrounded_fields  # noqa: E402
from harness.models import InvoiceExtraction  # noqa: E402
from harness.reference import Reference  # noqa: E402
from harness.text import po_key, supplier_similarity  # noqa: E402

GOOD = {"supplier_name": "Halvorsen Fasteners LLC", "invoice_number": "HF-10231", "invoice_date": "2026-10-02",
        "po_number": "PO-45000110", "currency": "USD",
        "lines": [{"description": "Exterior deck screws 3in, box of 350", "sku": "DS-300",
                   "quantity": 20, "unit_price": 18.5, "line_total": 370.0}],
        "subtotal": 936.25, "tax": 65.54, "total": 1001.79}


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self.body


class FakeSession:
    """Plays back canned Messages API responses and records the requests."""
    def __init__(self, *tool_inputs):
        self.queue, self.requests = list(tool_inputs), []

    def post(self, url, headers, json, timeout):
        self.requests.append(json)
        data = self.queue.pop(0)
        return FakeResponse({"content": [{"type": "tool_use", "id": f"t{len(self.requests)}",
                                          "name": "record_invoice", "input": data}]})


class TextHelpers(unittest.TestCase):
    def test_po_formats_normalise(self):
        self.assertEqual(po_key("PO-45000120"), po_key("4500-0120"))
        self.assertEqual(po_key("your order 4500 0132"), "45000132")
        self.assertEqual(po_key(None), "")

    def test_supplier_trading_name(self):
        self.assertGreater(supplier_similarity("RIDGELINE LUMBER CO.", "Ridgeline Lumber & Millwork"), 0.55)
        self.assertLess(supplier_similarity("Kowalski Hardware Supply", "Ridgeline Lumber & Millwork"), 0.2)


class Matching(unittest.TestCase):
    def test_line_without_sku_matches_on_description(self):
        ref = Reference.load()
        po = ref.po("PO-45000113")
        ex = InvoiceExtraction.model_validate({**GOOD, "lines": [
            {"description": "Exterior Deck Screws 3in - Box/350", "sku": None, "quantity": 25,
             "unit_price": 18.5, "line_total": 462.5}]})
        m = match_lines(ex, po)[0]
        self.assertEqual((m.po_line, m.method), (1, "description"))

    def test_unrelated_line_is_not_forced_onto_po(self):
        ref = Reference.load()
        po = ref.po("PO-45000121")
        ex = InvoiceExtraction.model_validate({**GOOD, "lines": [
            {"description": "Fuel surcharge", "sku": None, "quantity": 1, "unit_price": 35, "line_total": 35}]})
        self.assertIsNone(match_lines(ex, po)[0].po_line)


class Grounding(unittest.TestCase):
    def setUp(self):
        self.text = pdf_text(config.SAMPLES / "01_halvorsen_clean.pdf")

    def test_true_values_are_grounded(self):
        self.assertEqual(ungrounded_fields(InvoiceExtraction.model_validate(GOOD), self.text), [])

    def test_invented_po_is_caught(self):
        bad = InvoiceExtraction.model_validate({**GOOD, "po_number": "PO-45000113"})
        self.assertIn("po_number", ungrounded_fields(bad, self.text))

    def test_misread_price_is_caught(self):
        bad = json.loads(json.dumps(GOOD))
        bad["lines"][0]["unit_price"] = 16.5
        self.assertIn("lines[0].unit_price", ungrounded_fields(InvoiceExtraction.model_validate(bad), self.text))


class LLMExtractorContract(unittest.TestCase):
    def test_forces_tool_and_parses(self):
        s = FakeSession(GOOD)
        ex = LLMExtractor(api_key="test", session=s).extract("invoice text")
        self.assertEqual(ex.invoice_number, "HF-10231")
        req = s.requests[0]
        self.assertEqual(req["tool_choice"], {"type": "tool", "name": "record_invoice"})
        self.assertEqual(req["temperature"], 0)

    def test_repairs_once_after_schema_error(self):
        broken = {**GOOD, "lines": [{"description": "x", "sku": None, "quantity": "twenty",
                                     "unit_price": 1, "line_total": 1}]}
        s = FakeSession(broken, GOOD)
        ex = LLMExtractor(api_key="test", session=s).extract("invoice text")
        self.assertEqual(len(s.requests), 2)
        self.assertTrue(s.requests[1]["messages"][-1]["content"][0]["is_error"])
        self.assertEqual(ex.total, 1001.79)

    def test_gives_up_after_second_failure(self):
        broken = {**GOOD, "lines": [{"description": "x", "quantity": "?", "unit_price": 1}]}
        with self.assertRaises(ExtractionFailed):
            LLMExtractor(api_key="test", session=FakeSession(broken, broken)).extract("t")


class EndToEnd(unittest.TestCase):
    def test_no_unsafe_approvals_with_perfect_reading(self):
        sys.path.insert(0, str(ROOT / "evals"))
        from run_eval import run
        s = run("oracle")
        self.assertEqual(s["unsafe_approvals"], 0)
        self.assertEqual(s["decision_accuracy"], 1.0)

    def test_baseline_reader_fails_closed(self):
        sys.path.insert(0, str(ROOT / "evals"))
        from run_eval import run
        self.assertEqual(run("baseline")["unsafe_approvals"], 0)


if __name__ == "__main__":
    unittest.main()
