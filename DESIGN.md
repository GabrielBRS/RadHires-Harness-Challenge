# Invoice Check: design and delivery notes

## Problem thesis

An accounts-payable clerk at a small distributor repeatedly transcribes supplier PDF invoices,
then compares prices, receipt quantities and prior payments. Missing an exception can mean a
duplicate payment or paying for goods that never arrived; checking everything manually is slow.
For the fictional Lumen Supply Co., 250 invoices/week at three minutes each would consume 12.5
hours. That is a sizing hypothesis, not measured customer data or a promised saving.

The slice helps the clerk prepare one reviewed payment batch. AI fits variable document
layouts; arithmetic and payment policy belong in deterministic code. Already structured EDI
invoices do not need a model. No actual payments or outgoing emails occur in this prototype.

## The human workflow

A queue and a document review screen fit batch work better than a chat transcript. The clerk
uploads PDFs or loads synthetic examples, sees readiness/exception labels, and compares the
original PDF with extracted fields and plain-language findings. PDF.js renders inside React,
so inspection does not depend on a browser's native PDF plugin. The original remains downloadable.

Corrections use labelled fields and editable line items, require a note, rerun checks and clear
previous approval. The clerk can approve, hold, undo or edit a supplier email draft. Flagged
approvals require an explanation; records without basic identity/positive USD amounts cannot
be approved. The exported CSV includes approved records and review notes, escaping formula-like
text. The session lifecycle and explicit no-AI demo banner are visible to the user.

## Vertical architecture

The Rust composition root wires five features in `ai-backend/src/features`:

- `inbox`: upload/sample intake, queue, sequential processing and embedded memory KV storage.
- `agents`: MAX HTTP transport, Qwen reader/verifier prompts, JSON validation and traces.
- `checking`: invoice/reference types, PDF text, grounding, matching and deterministic rules.
- `review`: corrections, human decisions, audit notes and editable supplier drafts.
- `exporting`: approved CSV serialization and formula escaping.

React's `frontend/src/features/inbox` and `review` own their views. Shared API types, layout
and styling stay small. There are no global controllers/services/repositories layers. The same
Rust checking pipeline is invoked by the HTTP workflow, agent CLI and evaluation command.

Two application processes communicate through a local HTTP API: a Node static server hosts the
compiled React app on 3000 and proxies `/api` to Rust/Axum on 8080. MAX is the third process on
8000. Rust and MAX bind loopback; browser mutations reject cross-site requests. This is a local,
single-user prototype, without authentication or a production deployment claim.

Python is restricted to orchestration: `build.py` resolves paths, validates/downloads fixed
artifacts and builds the application; `run.py` checks readiness and supervises process groups.
Windows hands off to WSL. MAX's upstream Python/Mojo sources are third-party code, not a second
invoice backend. The default installs the official compiled runtime, with an explicit Bazel
source-build option. A source build is not a full compiler bootstrap; model graph compilation
happens at first startup.

## Harness and failure handling

```text
PDF text → Qwen reader → Rust schema validation → Qwen evidence verifier
         → grounding → supported scope → arithmetic → PO/supplier matching
         → paid/current-session duplicate checks → price/receipt checks
         → ready / review / hold recommendation → human decision → CSV
```

The two roles share one Qwen3-4B model and run sequentially. The verifier compares source text
with the extraction, can add concerns and cannot change values or remove deterministic findings.
This is bounded agent orchestration, not autonomous tool selection. Agreement between two
passes of the same model is not independent evidence of correctness.

Prompts request JSON and include schemas; Rust enforces types, unknown-field rejection,
finite/bounded numbers and lengths. A custom serving template disables Qwen thinking. Both
GPU and CPU modes use prompt-directed JSON rather than claiming grammar-constrained decoding.
Calls are capped at 2048 output tokens / 180 seconds. A conservative 5500-byte request-text
budget stays within an 8192 context with output allowance; oversized input is rejected rather
than truncated. Each role makes one bounded call, with no hidden retry. Partial/truncated or
invalid output fails closed. Verifier failure preserves the reader's extraction for review.

The checks reject missing identity/amounts, unsupported currency, invalid dates and negative
values. Grounding checks printed identifiers, dates and numeric evidence. It can miss a real
number copied into the wrong field, so arithmetic and reference comparison are still necessary.
An ungrounded PO is not matched. SKU matching precedes description matching. Price tolerance
is 0.5% / $0.01 and arithmetic tolerance $0.02. Duplicate numbers block; same supplier/total
within 14 days flags possible reissues. Receipt availability subtracts previously paid quantities.

A sequential worker limits inference concurrency and lets later queue items see earlier
results. Final checks, edits and approvals serialize through a mutation lock. Revision checks
prevent stale writes; clearing/removing a record while inference is running cannot resurrect it.
Approval rechecks current-session duplicates. Audit notes remain in the session, not on disk.

## Storage choice

SQLite is embedded with the Rust `rusqlite` bundled feature and `open_in_memory()`. A KV table
maps invoice UUIDs to JSON records and stores PDF bytes in a blob. No extra database process,
credentials or filesystem persistence is needed. Memory lasts until the backend shuts down,
not merely until the browser tab closes. This intentionally follows the requested ephemeral
session model; it is not an accounting ledger or durable audit trail. A 100-record / 200 MB
limit bounds input storage. Downloaded weights/source remain on disk independently of session data.

## Evaluation and delivery evidence

17 synthetic labelled PDFs cover three layouts, clean matches, price/quantity problems,
arithmetic errors, duplicated/reissued invoices, missing PO/SKU and a scan. The Rust oracle
checks match all 17 expected decisions and expected findings. Seven faults applied to each
of seven clean extractions produce 49 cases; all are prevented from being recommended ready.
These prove behavior on chosen fixtures, not real-world extraction accuracy.

The actual local Qwen evaluation matched **10/17 decisions**, with **zero unsafe approvals**.
All seven clean invoices were unnecessarily sent to review: six because the verifier flagged
equivalent formatting/descriptions, one because a reader response put null in a required
numeric field. There were 30 completed agent calls and one failed call, taking 66.4 seconds
in aggregate; the scan was rejected before inference. This is a known quality limitation,
not a successful extraction benchmark. The same synthetic cases informed prompt adjustments,
so these are development results, not a held-out evaluation. Full traces are in
`evals/results/live-max.json`; `summary.json` separates rules from model behavior.

The integration suite also checks actual PDF parsing, upload/review/edit/export routes,
invalid inputs, memory reset, stale revisions, CSV formula handling and the two-role HTTP
contract using fake responses. The application build and UI workflow are exercised locally.
`invoice-check eval --live` is the separate command for actual MAX/Qwen evaluation.

The Qwen snapshot was downloaded and hash-verified, and the official MAX runtime was installed
and exercised with actual local inference. The NVIDIA monitoring utility failed, but MAX's CUDA
backend was usable. Peak VRAM has not been measured. Source builds were attempted and rejected
by upstream LLVM checksum mismatches; the standard setup uses the official MAX distribution.
Windows/WSL and macOS scripts are documented and platform-aware, but were not exercised on
those operating systems here. CI runs Rust/React checks across all three OSes; GPU support
requires separate hardware validation.

## Tradeoffs and next steps

Rust provides one typed backend and an embedded session store; React supports corrections
without full-page reloads. This costs more setup than the original Python/HTML implementation,
but follows the requested stack. MAX adds a vendor compiler/runtime and platform-specific
requirements; its source download is for inspection and optional development builds, not a
reason to pretend all inference is pure Mojo. Qwen3-4B BF16 is smaller than unquantized 8B and
uses a format MAX supports. Its weights fit a sub-16 GB design budget; runtime headroom must
still be measured. Windows requires WSL, and Qwen Metal support remains unvalidated.

Reference POs, receipts and payment history are static synthetic fixtures. The demo regex reader
is intentionally limited and is never selected automatically. There is no OCR, multi-PO support,
credit-note support, receipt reservation across unpaid partial invoices, concurrent-user workflow,
automatic payment or sent email. CLI checks cannot inspect the UI's in-memory queue.

Time spent was not reliably recorded; the candidate should report their own actual effort.
AI assistance was used for implementation, checks and documentation. Next priorities are
50–100 authorized real invoices, actual Qwen accuracy/VRAM/latency measurements, evidence tied
to page coordinates, and receipt reservations. Durable storage would require a deliberate
change to the requested session lifecycle, plus access controls and audit retention.
