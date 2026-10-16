"""Generate the synthetic world the prototype runs on.

Lumen Supply Co. is a fictional 3-branch building-supplies distributor.
This script writes:

  data/purchase_orders.json   POs with ordered / received quantities (the "ERP")
  data/paid_invoices.json     invoices already paid (for duplicate + partial-billing checks)
  data/samples/*.pdf          17 supplier invoices in 3 very different layouts
  data/samples/ground_truth.json  what each invoice really says + the issues planted in it

Every discrepancy is planted on purpose so the eval knows the right answer.
Run:  python data/generate.py
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"

SUPPLIERS = {
    "S001": {"name": "Halvorsen Fasteners LLC", "addr": "1180 Industrial Pkwy, Dayton, OH 45414"},
    "S002": {"name": "Ridgeline Lumber & Millwork", "addr": "77 Mill Road, Asheville, NC 28806"},
    "S003": {"name": "Kowalski Hardware Supply", "addr": "52 Third Street, Erie, PA 16501"},
}
TAX = 0.07


def pol(line, sku, desc, ordered, received, price):
    return {"line": line, "sku": sku, "description": desc, "qty_ordered": ordered,
            "qty_received": received, "unit_price": price}


PURCHASE_ORDERS = [
    {"po_number": "PO-45000105", "supplier_id": "S001", "order_date": "2026-09-08", "lines": [
        pol(1, "HB-100", "Hex bolts 3/8 x 4in (box of 50)", 10, 10, 21.15)]},
    {"po_number": "PO-45000106", "supplier_id": "S001", "order_date": "2026-09-15", "lines": [
        pol(1, "WS-050", "Flat washers 1/2in (box of 100)", 20, 20, 9.75)]},
    {"po_number": "PO-45000110", "supplier_id": "S001", "order_date": "2026-09-22", "lines": [
        pol(1, "DS-300", "Exterior deck screws 3in, box of 350", 20, 20, 18.50),
        pol(2, "LB-512", "Lag bolts 1/2 x 12in (box of 25)", 10, 10, 42.00),
        pol(3, "WS-050", "Flat washers 1/2in (box of 100)", 15, 15, 9.75)]},
    {"po_number": "PO-45000111", "supplier_id": "S001", "order_date": "2026-09-23", "lines": [
        pol(1, "DS-250", "Exterior deck screws 2.5in, box of 350", 30, 30, 16.20),
        pol(2, "AN-375", "Wedge anchors 3/8 x 3in (box of 25)", 12, 8, 27.40)]},
    {"po_number": "PO-45000112", "supplier_id": "S001", "order_date": "2026-09-24", "lines": [
        pol(1, "SS-200", "Stainless screws #10 x 2in (box of 100)", 10, 10, 34.90),
        pol(2, "HB-100", "Hex bolts 3/8 x 4in (box of 50)", 20, 20, 21.15)]},
    {"po_number": "PO-45000113", "supplier_id": "S001", "order_date": "2026-09-25", "lines": [
        pol(1, "DS-300", "Exterior deck screws 3in, box of 350", 25, 25, 18.50),
        pol(2, "LB-512", "Lag bolts 1/2 x 12in (box of 25)", 6, 6, 42.00)]},
    {"po_number": "PO-45000114", "supplier_id": "S001", "order_date": "2026-09-28", "lines": [
        pol(1, "SS-200", "Stainless screws #10 x 2in (box of 100)", 12, 12, 34.90),
        pol(2, "AN-375", "Wedge anchors 3/8 x 3in (box of 25)", 10, 6, 27.40)]},
    {"po_number": "PO-45000140", "supplier_id": "S001", "order_date": "2026-09-10", "lines": [
        pol(1, "DS-300", "Exterior deck screws 3in, box of 350", 20, 20, 18.50)]},
    {"po_number": "PO-45000120", "supplier_id": "S002", "order_date": "2026-09-21", "lines": [
        pol(1, "PT-2612", "Pressure-treated lumber 2x6x12", 40, 40, 14.85),
        pol(2, "PT-2412", "Pressure-treated lumber 2x4x12", 60, 60, 9.60),
        pol(3, "CD-5408", "Cedar decking 5/4x6x8", 50, 50, 17.30)]},
    {"po_number": "PO-45000121", "supplier_id": "S002", "order_date": "2026-09-22", "lines": [
        pol(1, "PLY-34", "Plywood 3/4in CDX 4x8", 25, 25, 48.75),
        pol(2, "OSB-716", "OSB sheathing 7/16in 4x8", 30, 30, 22.40)]},
    {"po_number": "PO-45000122", "supplier_id": "S002", "order_date": "2026-09-24", "lines": [
        pol(1, "TRM-114", "Primed pine trim 1x4x16", 20, 20, 18.90),
        pol(2, "BB-358", "Colonial baseboard 3-5/8in x 16ft", 15, 15, 24.60)]},
    {"po_number": "PO-45000123", "supplier_id": "S002", "order_date": "2026-09-29", "lines": [
        pol(1, "PT-2412", "Pressure-treated lumber 2x4x12", 80, 80, 9.60),
        pol(2, "PLY-34", "Plywood 3/4in CDX 4x8", 10, 10, 48.75)]},
    {"po_number": "PO-45000124", "supplier_id": "S002", "order_date": "2026-09-30", "lines": [
        pol(1, "CD-5408", "Cedar decking 5/4x6x8", 40, 40, 17.30)]},
    {"po_number": "PO-45000131", "supplier_id": "S003", "order_date": "2026-09-22", "lines": [
        pol(1, "JH-206", "Joist hanger 2x6, galvanized", 50, 50, 3.10),
        pol(2, "HT-12", "Hurricane tie H2.5A", 40, 40, 1.85),
        pol(3, "PB-44", "Post base 4x4, adjustable", 10, 10, 12.60)]},
    {"po_number": "PO-45000132", "supplier_id": "S003", "order_date": "2026-09-26", "lines": [
        pol(1, "CN-16D", "16d common nails, 50 lb box", 6, 6, 79.00),
        pol(2, "RS-24", "Rafter tie RT-7", 30, 30, 2.45)]},
]
for po in PURCHASE_ORDERS:
    po["supplier_name"] = SUPPLIERS[po["supplier_id"]]["name"]
    po["tax_rate"] = TAX

PAID_INVOICES = [
    {"supplier_id": "S001", "invoice_number": "HF-10198", "invoice_date": "2026-09-15",
     "po_number": "PO-45000105", "total": 226.31, "lines": [{"sku": "HB-100", "quantity": 10}]},
    {"supplier_id": "S001", "invoice_number": "HF-10171", "invoice_date": "2026-09-28",
     "po_number": "PO-45000106", "total": 208.65, "lines": [{"sku": "WS-050", "quantity": 20}]},
    {"supplier_id": "S001", "invoice_number": "HF-10207", "invoice_date": "2026-09-18",
     "po_number": "PO-45000140", "total": 237.54, "lines": [{"sku": "DS-300", "quantity": 12}]},
]


def L(desc, qty, price, sku=None, printed_total=None):
    total = round(qty * price, 2) if printed_total is None else printed_total
    return {"description": desc, "sku": sku, "quantity": qty, "unit_price": price, "line_total": total}


# Each scenario: what is printed on the page + what the harness should conclude.
SCENARIOS = [
    dict(file="01_halvorsen_clean.pdf", layout="A", supplier="S001", number="HF-10231", date="2026-10-02",
         po_printed="PO-45000110", po_true="PO-45000110",
         lines=[L("Exterior deck screws 3in, box of 350", 20, 18.50, "DS-300"),
                L("Lag bolts 1/2 x 12in (box of 25)", 10, 42.00, "LB-512"),
                L("Flat washers 1/2in (box of 100)", 15, 9.75, "WS-050")],
         expect=[], decision="approve", why="Clean three-way match."),
    dict(file="02_ridgeline_clean.pdf", layout="B", supplier="S002", number="RL-55870", date="2026-10-01",
         po_printed="4500-0120", po_true="PO-45000120",
         lines=[L("Pressure-treated 2x6x12", 40, 14.85, "PT-2612"),
                L("Pressure-treated 2x4x12", 60, 9.60, "PT-2412"),
                L("Cedar decking 5/4x6x8", 50, 17.30, "CD-5408")],
         expect=[], decision="approve", why="Clean; PO reference printed in the supplier's own format."),
    dict(file="03_halvorsen_price_up.pdf", layout="A", supplier="S001", number="HF-10240", date="2026-10-05",
         po_printed="PO-45000112", po_true="PO-45000112",
         lines=[L("Stainless screws #10 x 2in (box of 100)", 10, 36.40, "SS-200"),
                L("Hex bolts 3/8 x 4in (box of 50)", 20, 21.15, "HB-100")],
         expect=["PRICE_VARIANCE"], decision="review", why="Unit price $1.50 above PO on SS-200."),
    dict(file="04_halvorsen_short_delivery.pdf", layout="A", supplier="S001", number="HF-10236", date="2026-10-04",
         po_printed="PO-45000111", po_true="PO-45000111",
         lines=[L("Exterior deck screws 2.5in, box of 350", 30, 16.20, "DS-250"),
                L("Wedge anchors 3/8 x 3in (box of 25)", 12, 27.40, "AN-375")],
         expect=["QTY_EXCEEDS_RECEIVED"], decision="review", why="Billed 12 anchors, warehouse received 8."),
    dict(file="05_ridgeline_surcharge.pdf", layout="B", supplier="S002", number="RL-55902", date="2026-10-06",
         po_printed="4500-0121", po_true="PO-45000121",
         lines=[L("Plywood 3/4 CDX 4x8", 25, 48.75, "PLY-34"),
                L("OSB 7/16 4x8", 30, 22.40, "OSB-716"),
                L("Fuel surcharge", 1, 35.00, "FS-01")],
         expect=["ITEM_NOT_ON_PO"], decision="review", why="Fuel surcharge was never on the PO."),
    dict(file="06_ridgeline_math_error.pdf", layout="B", supplier="S002", number="RL-55911", date="2026-10-07",
         po_printed="4500-0122", po_true="PO-45000122",
         lines=[L("Primed pine trim 1x4x16", 20, 18.90, "TRM-114", printed_total=387.00),
                L("Colonial baseboard 3-5/8 x 16", 15, 24.60, "BB-358")],
         expect=["MATH_ERROR"], decision="review", why="20 x $18.90 is $378.00, printed $387.00."),
    dict(file="07_halvorsen_resent_duplicate.pdf", layout="A", supplier="S001", number="HF-10198", date="2026-09-15",
         po_printed="PO-45000105", po_true="PO-45000105",
         lines=[L("Hex bolts 3/8 x 4in (box of 50)", 10, 21.15, "HB-100")],
         expect=["DUPLICATE_INVOICE"], decision="hold", why="HF-10198 was already paid on 2026-09-15."),
    dict(file="08_kowalski_no_po.pdf", layout="C", supplier="S003", number="KH-0412", date="2026-10-08",
         po_printed=None, po_true=None,
         lines=[L("16d common nails, 50 lb box", 6, 79.00, "CN-16D"),
                L("Rafter ties RT-7", 30, 2.45, "RS-24")],
         expect=["PO_NOT_FOUND"], decision="hold", why="No PO reference anywhere on the invoice."),
    dict(file="09_kowalski_clean.pdf", layout="C", supplier="S003", number="KH-0398", date="2026-10-03",
         po_printed="45000131", po_true="PO-45000131",
         lines=[L("galvanized joist hangers 2x6", 50, 3.10, "JH-206"),
                L("hurricane ties H2.5A", 40, 1.85, "HT-12"),
                L("adjustable 4x4 post bases", 10, 12.60, "PB-44")],
         expect=[], decision="approve", why="Clean, but written as a letter instead of a table."),
    dict(file="10_kowalski_price_up.pdf", layout="C", supplier="S003", number="KH-0405", date="2026-10-06",
         po_printed="4500 0132", po_true="PO-45000132",
         lines=[L("16d common nails, 50 lb box", 6, 84.50, "CN-16D"),
                L("rafter ties RT-7", 30, 2.45, "RS-24")],
         expect=["PRICE_VARIANCE"], decision="review", why="Nails billed at $84.50, PO says $79.00."),
    dict(file="11_halvorsen_partial_billing.pdf", layout="A", supplier="S001", number="HF-10252", date="2026-10-09",
         po_printed="PO-45000140", po_true="PO-45000140",
         lines=[L("Exterior deck screws 3in, box of 350", 8, 18.50, "DS-300")],
         expect=[], decision="approve", why="Second partial bill: 12 already paid + 8 now = 20 received."),
    dict(file="12_ridgeline_name_variant.pdf", layout="B", supplier="S002", number="RL-55925", date="2026-10-09",
         supplier_printed="RIDGELINE LUMBER CO.", po_printed="4500-0123", po_true="PO-45000123",
         lines=[L("Pressure-treated 2x4x12", 80, 9.60, "PT-2412"),
                L("Plywood 3/4 CDX 4x8", 10, 48.75, "PLY-34")],
         expect=[], decision="approve", why="Supplier prints a shorter trading name; should still match."),
    dict(file="13_halvorsen_missing_sku.pdf", layout="A", supplier="S001", number="HF-10259", date="2026-10-10",
         po_printed="PO-45000113", po_true="PO-45000113",
         lines=[L("Exterior Deck Screws 3in - Box/350", 25, 18.50, None),
                L("Lag bolts 1/2 x 12in (box of 25)", 6, 42.00, "LB-512")],
         expect=[], decision="approve", why="One line has no item code; must match on description."),
    dict(file="14_halvorsen_two_problems.pdf", layout="A", supplier="S001", number="HF-10263", date="2026-10-12",
         po_printed="PO-45000114", po_true="PO-45000114",
         lines=[L("Stainless screws #10 x 2in (box of 100)", 12, 35.60, "SS-200"),
                L("Wedge anchors 3/8 x 3in (box of 25)", 10, 27.40, "AN-375")],
         expect=["PRICE_VARIANCE", "QTY_EXCEEDS_RECEIVED"], decision="review",
         why="Price up on screws AND anchors billed before they arrived."),
    dict(file="15_ridgeline_price_down.pdf", layout="B", supplier="S002", number="RL-55930", date="2026-10-13",
         po_printed="4500-0124", po_true="PO-45000124",
         lines=[L("Cedar decking 5/4x6x8", 40, 16.90, "CD-5408")],
         expect=[], decision="approve", why="Billed below PO price - in our favour, not a blocker."),
    dict(file="16_halvorsen_reissued.pdf", layout="A", supplier="S001", number="HF-10175", date="2026-10-01",
         po_printed="PO-45000106", po_true="PO-45000106",
         lines=[L("Flat washers 1/2in (box of 100)", 20, 9.75, "WS-050")],
         expect=["POSSIBLE_DUPLICATE", "QTY_EXCEEDS_RECEIVED"], decision="hold",
         why="Same PO, same amount as HF-10171 paid 3 days earlier, new invoice number."),
    dict(file="17_scanned_fax.pdf", layout="SCAN", supplier="S003", number="KH-0415", date="2026-10-14",
         po_printed="45000131", po_true="PO-45000131",
         lines=[L("joist hangers 2x6", 10, 3.10, "JH-206")],
         expect=["UNREADABLE"], decision="review", why="Faxed/scanned page, no text layer."),
]


def money(v: float) -> str:
    return f"${v:,.2f}"


def qty_str(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else str(q)


def totals(lines):
    sub = round(sum(l["line_total"] for l in lines), 2)
    tax = round(sub * TAX, 2)
    return sub, tax, round(sub + tax, 2)


def human_date(iso: str) -> str:
    import datetime as dt
    return dt.date.fromisoformat(iso).strftime("%B %-d, %Y")


# ---------------------------------------------------------------------------
# Layout A: classic table (Halvorsen)
# ---------------------------------------------------------------------------
def layout_a(c, s):
    sup = SUPPLIERS[s["supplier"]]
    c.setFont("Helvetica-Bold", 16); c.drawString(50, 740, sup["name"])
    c.setFont("Helvetica", 9); c.drawString(50, 726, sup["addr"])
    c.setFont("Helvetica-Bold", 22); c.drawRightString(560, 740, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(50, 690, "Bill To: Lumen Supply Co., Accounts Payable, 400 Commerce Dr, Columbus, OH 43215")
    c.drawString(50, 668, f"Invoice No: {s['number']}")
    c.drawString(50, 654, f"Invoice Date: {s['date']}")
    if s["po_printed"]:
        c.drawString(50, 640, f"PO Number: {s['po_printed']}")
    c.drawString(330, 668, "Terms: Net 30")
    y = 605
    c.setFont("Helvetica-Bold", 10)
    for x, h in [(50, "SKU"), (120, "Description")]:
        c.drawString(x, y, h)
    for x, h in [(410, "Qty"), (485, "Unit Price"), (560, "Amount")]:
        c.drawRightString(x, y, h)
    c.line(50, y - 4, 560, y - 4)
    c.setFont("Helvetica", 10)
    for ln in s["lines"]:
        y -= 18
        c.drawString(50, y, ln["sku"] or "")
        c.drawString(120, y, ln["description"])
        c.drawRightString(410, y, qty_str(ln["quantity"]))
        c.drawRightString(485, y, money(ln["unit_price"]))
        c.drawRightString(560, y, money(ln["line_total"]))
    sub, tax, tot = totals(s["lines"])
    y -= 30
    c.line(380, y + 14, 560, y + 14)
    for label, v in [("Subtotal", sub), ("Sales Tax (7%)", tax), ("Total Due", tot)]:
        c.setFont("Helvetica-Bold" if label == "Total Due" else "Helvetica", 10)
        c.drawString(380, y, label); c.drawRightString(560, y, money(v)); y -= 16
    c.setFont("Helvetica", 8)
    c.drawString(50, 80, "Remit to: Halvorsen Fasteners LLC, PO Box 2210, Dayton OH. Thank you for your business.")


# ---------------------------------------------------------------------------
# Layout B: different labels, header block on the right (Ridgeline)
# ---------------------------------------------------------------------------
def layout_b(c, s):
    sup = SUPPLIERS[s["supplier"]]
    name = s.get("supplier_printed", sup["name"])
    c.setFont("Times-Bold", 18); c.drawString(50, 740, name)
    c.setFont("Times-Roman", 10); c.drawString(50, 726, sup["addr"])
    c.setFont("Times-Bold", 12); c.drawString(380, 740, "STATEMENT OF CHARGES")
    c.setFont("Times-Roman", 10)
    c.drawString(380, 722, f"Bill #  {s['number']}")
    c.drawString(380, 708, f"Dated  {human_date(s['date'])}")
    c.drawString(380, 694, f"Your Order Ref  {s['po_printed']}")
    c.drawString(50, 694, "Customer: LUMEN SUPPLY CO")
    c.drawString(50, 680, "Ship to: Branch 2 - Westerville yard")
    y = 640
    c.setFont("Times-Bold", 10)
    c.drawString(50, y, "Item Code"); c.drawString(130, y, "Item")
    c.drawRightString(400, y, "Units"); c.drawRightString(480, y, "Rate"); c.drawRightString(560, y, "Line Total")
    c.setFont("Times-Roman", 10)
    for ln in s["lines"]:
        y -= 17
        c.drawString(50, y, ln["sku"] or "")
        c.drawString(130, y, ln["description"])
        c.drawRightString(400, y, f"{qty_str(ln['quantity'])} ea")
        c.drawRightString(480, y, f"{ln['unit_price']:,.2f}")
        c.drawRightString(560, y, f"{ln['line_total']:,.2f}")
    sub, tax, tot = totals(s["lines"])
    y -= 34
    c.drawString(380, y, "Goods"); c.drawRightString(560, y, f"{sub:,.2f}"); y -= 15
    c.drawString(380, y, "Tax 7.00%"); c.drawRightString(560, y, f"{tax:,.2f}"); y -= 15
    c.setFont("Times-Bold", 11)
    c.drawString(380, y, "AMOUNT DUE (USD)"); c.drawRightString(560, y, f"{tot:,.2f}")
    c.setFont("Times-Italic", 9)
    c.drawString(50, 90, "Payment due within 30 days. Late payments subject to 1.5% monthly service charge.")


# ---------------------------------------------------------------------------
# Layout C: a small supplier who types invoices as a letter (Kowalski)
# ---------------------------------------------------------------------------
def layout_c(c, s):
    sup = SUPPLIERS[s["supplier"]]
    sub, tax, tot = totals(s["lines"])
    c.setFont("Courier-Bold", 13); c.drawString(50, 740, sup["name"].upper())
    c.setFont("Courier", 10); c.drawString(50, 726, sup["addr"] + "  -  call Stan 814-555-0142")
    t = c.beginText(50, 690); t.setFont("Courier", 10); t.setLeading(15)
    ref = (f"for your order {s['po_printed']}" if s["po_printed"]
           else "for the nails and ties Mike called in last week")
    body = [
        human_date(s["date"]),
        "",
        "Hi Lumen Supply folks,",
        "",
        f"Here is bill number {s['number']} {ref}.",
        "Everything was dropped at your Columbus dock. Here's what we sent:",
        "",
    ]
    for ln in s["lines"]:
        code = f" (item {ln['sku']})" if ln["sku"] else ""
        body.append(f"  - {qty_str(ln['quantity'])} {ln['description']}{code}")
        body.append(f"      at {money(ln['unit_price'])} each, comes to {money(ln['line_total'])}")
    body += [
        "",
        f"That's {money(sub)} for the goods, plus {money(tax)} Ohio sales tax,",
        f"so please send {money(tot)} total. Check or ACH is fine, 30 days.",
        "",
        "Thanks as always,",
        "Stan Kowalski",
    ]
    for b in body:
        t.textLine(b)
    c.drawText(t)


def layout_scan(path: Path, s):
    """Render an invoice as a raster image only (like a fax) - no text layer."""
    img = Image.new("L", (1275, 1650), 255)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    sub, tax, tot = totals(s["lines"])
    lines = [SUPPLIERS[s["supplier"]]["name"].upper(), "", f"INVOICE {s['number']}   {s['date']}",
             f"PO {s['po_printed']}", ""]
    for ln in s["lines"]:
        lines.append(f"{qty_str(ln['quantity'])} x {ln['description']} @ {money(ln['unit_price'])} = {money(ln['line_total'])}")
    lines += ["", f"TOTAL {money(tot)}"]
    y = 120
    for ln in lines:
        d.text((100, y), ln, fill=30, font=font); y += 44
    img = img.rotate(1.2, fillcolor=235)  # slightly crooked, like a real fax
    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.drawImage(ImageReader(buf), 0, 0, width=612, height=792)
    c.showPage(); c.save()


def main():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    (ROOT / "purchase_orders.json").write_text(json.dumps(PURCHASE_ORDERS, indent=2))
    (ROOT / "paid_invoices.json").write_text(json.dumps(PAID_INVOICES, indent=2))
    truth = []
    for s in SCENARIOS:
        path = SAMPLES / s["file"]
        if s["layout"] == "SCAN":
            layout_scan(path, s)
        else:
            c = canvas.Canvas(str(path), pagesize=LETTER)
            c.setTitle(f"Invoice {s['number']}")
            {"A": layout_a, "B": layout_b, "C": layout_c}[s["layout"]](c, s)
            c.showPage(); c.save()
        sub, tax, tot = totals(s["lines"])
        truth.append({
            "file": s["file"],
            "layout": s["layout"],
            "why": s["why"],
            "expected_issues": sorted(s["expect"]),
            "expected_decision": s["decision"],
            "expected_po": s["po_true"],
            "extraction": {
                "supplier_name": s.get("supplier_printed", SUPPLIERS[s["supplier"]]["name"]),
                "invoice_number": s["number"],
                "invoice_date": s["date"],
                "po_number": s["po_printed"],
                "currency": "USD",
                "lines": s["lines"],
                "subtotal": sub, "tax": tax, "total": tot,
            },
        })
    (SAMPLES / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"Wrote {len(PURCHASE_ORDERS)} POs, {len(PAID_INVOICES)} paid invoices, {len(truth)} sample invoices.")


if __name__ == "__main__":
    main()
