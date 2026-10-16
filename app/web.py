"""Invoice Check - the web screen an accounts-payable clerk uses.

    python -m app.web        then open http://localhost:5000
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template, request, send_file, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.queue_store import Queue  # noqa: E402
from harness import config  # noqa: E402
from harness.checks import describe_field  # noqa: E402
from harness.explain import supplier_email  # noqa: E402
from harness.models import CheckResult  # noqa: E402
from harness.reference import Reference  # noqa: E402
from harness.text import money, qty  # noqa: E402

app = Flask(__name__)
app.jinja_env.filters.update(money=money, qty=qty)
queue: Queue | None = None

LABELS = {"approve": "Ready to approve", "review": "Needs a look", "hold": "Don't pay yet"}
ACTIONS = {"approved": "Approved for payment", "held": "On hold", "queried": "Queried with supplier"}


def get_queue() -> Queue:
    global queue
    if queue is None:
        queue = Queue()
    return queue


def view(it: dict) -> dict:
    """Flatten a queue item for templates."""
    r = CheckResult.model_validate(it["result"]) if it.get("result") else None
    ex = r.extraction if r else None
    return dict(
        it=it, r=r, ex=ex,
        decision=r.decision if r else None,
        label=LABELS.get(r.decision, "") if r else ("Reading…" if it["status"] in ("waiting", "reading")
                                                     else "Something went wrong"),
        supplier=(ex.supplier_name if ex and ex.supplier_name else it["name"]),
        number=ex.invoice_number if ex else None,
        total=ex.total if ex else None,
        action_label=ACTIONS.get(it.get("action") or "", ""),
    )


@app.get("/")
def inbox():
    q = get_queue()
    rows = [view(i) for i in q.items]
    show = request.args.get("show", "all")
    counts = {k: sum(1 for r in rows if r["decision"] == k and not r["it"]["action"])
              for k in ("approve", "review", "hold")}
    counts["done"] = sum(1 for r in rows if r["it"]["action"])
    pending = sum(1 for r in rows if r["it"]["status"] in ("waiting", "reading"))
    if show in ("approve", "review", "hold"):
        rows = [r for r in rows if r["decision"] == show and not r["it"]["action"]]
    elif show == "done":
        rows = [r for r in rows if r["it"]["action"]]
    order = {"hold": 0, "review": 1, "approve": 2, None: -1}
    rows.sort(key=lambda r: (bool(r["it"]["action"]), order[r["decision"]]))
    return render_template("inbox.html", rows=rows, counts=counts, pending=pending, show=show,
                           extractor=q.extractor_kind, model=config.ANTHROPIC_MODEL)


@app.post("/upload")
def upload():
    q = get_queue()
    for f in request.files.getlist("files"):
        if not f.filename.lower().endswith(".pdf"):
            continue
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            f.save(tmp.name)
            q.add_file(Path(tmp.name), f.filename)
        os.unlink(tmp.name)
    return redirect(url_for("inbox"))


@app.post("/samples")
def samples():
    q = get_queue()
    for p in sorted(config.SAMPLES.glob("*.pdf")):
        q.add_file(p, p.name)
    return redirect(url_for("inbox"))


@app.post("/reset")
def reset():
    get_queue().reset()
    return redirect(url_for("inbox"))


@app.post("/approve-ready")
def approve_ready():
    q = get_queue()
    for it in q.items:
        if it.get("result") and it["result"]["decision"] == "approve" and not it["action"]:
            q.act(it["id"], "approved", "Bulk-approved: no issues found")
    return redirect(url_for("inbox"))


@app.get("/invoice/<iid>")
def detail(iid):
    q = get_queue()
    it = q.get(iid) or abort(404)
    v = view(it)
    r, ex = v["r"], v["ex"]
    po_lines, line_rows = {}, []
    if r and r.po_number_matched:
        po = Reference.load().po(r.po_number_matched)
        po_lines = {l.line: l for l in po.lines}
    if ex:
        issue_by_line = {}
        for i in r.issues:
            if i.line_index is not None:
                issue_by_line.setdefault(i.line_index, []).append(i)
        matches = {m.invoice_index: m for m in r.line_matches}
        for idx, ln in enumerate(ex.lines):
            m = matches.get(idx)
            pl = po_lines.get(m.po_line) if m and m.po_line else None
            line_rows.append(dict(ln=ln, m=m, pl=pl, issues=issue_by_line.get(idx, []),
                                  unconfirmed={f.split(".")[-1] for f in r.ungrounded_fields
                                               if f.startswith(f"lines[{idx}]")}))
    try:
        import pypdfium2 as pdfium
        pages = min(len(pdfium.PdfDocument(it["path"])), 5)
    except Exception:
        pages = 1
    ids = [x["id"] for x in q.items]
    pos = ids.index(iid)
    nxt = next((x["id"] for x in q.items[pos + 1:] if not x["action"]), None)
    return render_template(
        "detail.html", v=v, r=r, ex=ex, line_rows=line_rows,
        unconfirmed=set(r.ungrounded_fields) if r else set(),
        unconfirmed_names=[describe_field(f, ex) for f in r.ungrounded_fields] if r and ex else [],
        email=supplier_email(r) if r else None, next_id=nxt, pages=pages,
        needs_note=bool(r and r.decision != "approve"), labels=LABELS, actions=ACTIONS)


@app.get("/invoice/<iid>/pdf")
def pdf(iid):
    it = get_queue().get(iid) or abort(404)
    return send_file(it["path"], mimetype="application/pdf")


@app.get("/invoice/<iid>/page/<int:n>.png")
def page_image(iid, n):
    """Render a PDF page as an image, so the invoice shows in every browser (incl. mobile)."""
    import pypdfium2 as pdfium
    it = get_queue().get(iid) or abort(404)
    doc = pdfium.PdfDocument(it["path"])
    if n >= len(doc):
        abort(404)
    img = doc[n].render(scale=2).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=3600)


@app.post("/invoice/<iid>/act")
def act(iid):
    q = get_queue()
    it = q.get(iid) or abort(404)
    action = request.form.get("action")
    note = request.form.get("note", "")
    if action not in ACTIONS and action != "undo":
        abort(400)
    if action == "undo":
        q.act(iid, None, "Decision undone")
        return redirect(url_for("detail", iid=iid))
    flagged = it.get("result") and it["result"]["decision"] != "approve"
    if action == "approved" and flagged and len(note.strip()) < 5:
        return redirect(url_for("detail", iid=iid, need_note=1))
    q.act(iid, action, note)
    nxt = request.form.get("next")
    return redirect(url_for("detail", iid=nxt) if nxt else url_for("inbox"))


@app.get("/export.csv")
def export():
    """Approved batch in a flat format an ERP import can take (stubbed integration)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["supplier", "invoice_number", "invoice_date", "po_number", "total", "approved_at", "note"])
    for it in get_queue().items:
        if it.get("action") != "approved" or not it.get("result"):
            continue
        ex = it["result"]["extraction"] or {}
        w.writerow([ex.get("supplier_name"), ex.get("invoice_number"), ex.get("invoice_date"),
                    it["result"].get("po_number_matched"), ex.get("total"), it["acted_at"], it["note"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=approved_invoices.csv"})


@app.get("/status")
def status():
    q = get_queue()
    return {"pending": sum(1 for i in q.items if i["status"] in ("waiting", "reading"))}


if __name__ == "__main__":
    get_queue()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
