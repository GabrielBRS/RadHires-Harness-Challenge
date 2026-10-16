"""The clerk's work queue: a JSON file plus one background worker.

Uploads return immediately; a single worker thread reads invoices one at a time
(sequential on purpose, so "already in your queue" duplicate checks are reliable).
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import threading
import traceback
import uuid
from pathlib import Path

from harness import config
from harness.extract import make_extractor
from harness.models import CheckResult
from harness.pipeline import check_invoice
from harness.reference import Reference

RUNTIME = config.RUNTIME
STATE = RUNTIME / "state.json"
UPLOADS = RUNTIME / "uploads"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


class Queue:
    def __init__(self, extractor_kind: str | None = None):
        UPLOADS.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.extractor_kind = extractor_kind or config.EXTRACTOR
        self.extractor = make_extractor(self.extractor_kind)
        self.items: list[dict] = json.loads(STATE.read_text()) if STATE.exists() else []
        for it in self.items:                       # survive a restart mid-batch
            if it["status"] == "reading":
                it["status"] = "waiting"
        threading.Thread(target=self._worker, daemon=True).start()
        self.wake.set()

    # ---- persistence ---------------------------------------------------
    def _save(self):
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=1))
        tmp.replace(STATE)

    # ---- intake --------------------------------------------------------
    def add_file(self, src: Path, original_name: str) -> str:
        iid = uuid.uuid4().hex[:8]
        dest = UPLOADS / f"{iid}_{Path(original_name).name}"
        shutil.copyfile(src, dest)
        with self.lock:
            self.items.append(dict(id=iid, name=Path(original_name).name, path=str(dest), status="waiting",
                                   added=_now(), result=None, action=None, note="", acted_at=None, log=[]))
            self._save()
        self.wake.set()
        return iid

    def get(self, iid: str) -> dict | None:
        return next((i for i in self.items if i["id"] == iid), None)

    def reset(self):
        with self.lock:
            self.items = []
            shutil.rmtree(UPLOADS, ignore_errors=True)
            UPLOADS.mkdir(parents=True, exist_ok=True)
            self._save()

    # ---- clerk actions -------------------------------------------------
    def act(self, iid: str, action: str, note: str = "") -> None:
        with self.lock:
            it = self.get(iid)
            it["action"], it["note"], it["acted_at"] = action, note.strip(), _now()
            it["log"].append({"at": it["acted_at"], "action": action, "note": note.strip()})
            self._save()

    # ---- worker ----------------------------------------------------------
    def _queue_snapshot(self, exclude: str) -> list[dict]:
        out = []
        for it in self.items:
            r = it.get("result")
            if it["id"] == exclude or not r or not r.get("extraction") or not r.get("supplier_id"):
                continue
            ex = r["extraction"]
            out.append(dict(supplier_id=r["supplier_id"], invoice_number=ex.get("invoice_number"),
                            invoice_date=ex.get("invoice_date"), total=ex.get("total") or 0.0))
        return out

    def _worker(self):
        while True:
            self.wake.wait()
            with self.lock:
                nxt = next((i for i in self.items if i["status"] == "waiting"), None)
                if not nxt:
                    self.wake.clear()
                    continue
                nxt["status"] = "reading"
                self._save()
            try:
                ref = Reference.load()
                ref.in_queue = self._queue_snapshot(nxt["id"])
                result: CheckResult = check_invoice(nxt["path"], self.extractor, ref)
                payload, status = result.model_dump(), "done"
            except Exception:  # never lose an invoice because of a bug
                payload, status = None, "error"
                nxt["error"] = traceback.format_exc(limit=2)
            with self.lock:
                nxt["result"], nxt["status"] = payload, status
                self._save()
