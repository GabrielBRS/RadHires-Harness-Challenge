# Invoice Check

A local workspace for an accounts-payable clerk to compare supplier PDF invoices with
purchase orders, goods received and payment history. Review exceptions beside the original
PDF, correct fields, record a decision and export approved invoices. Nothing is emailed or paid.

**React frontend + Rust backend + embedded, in-memory SQLite KV.** Two application services
(3000 and 8080), plus MAX/Qwen inference (8000). Python is used only by `build.py` and `run.py`.
The third-party MAX runtime itself contains Python serving code and Mojo kernels.

## Start here

Install **Python 3.10–3.14**, **Node.js 24**, **Rust 1.97.1**, Git and a C compiler.
The full AI path needs a supported MAX device; Windows runs through WSL2. Installation
commands and download links for Linux, Windows and macOS are in [LOCAL_AI.md](docs/LOCAL_AI.md).

```bash
# From the cloned repository, build React/Rust and prepare the local MAX runtime + Qwen.
python3 build.py

# Start all three services. Open http://localhost:3000.
python3 run.py
```

`run.py` preloads the 17 sample invoices before opening the frontend, so they are visible
immediately while local processing continues in the background. Use `python3 run.py --empty`
to start with a blank session for real uploads.

On native Windows use `py build.py` and `py run.py`; both forward to the default WSL
Linux distribution, where the prerequisites must also be installed. Prefer cloning and
running directly inside WSL for filesystem performance.

The build downloads the MAX **source code** into `inference/source/` and the official compiled
MAX distribution into `inference/.venv/`. The Qwen snapshot goes into `models/Qwen3-4B/`.
Paths are anchored to the scripts, never the terminal's current directory. For example,
`python3 /path/to/invoice-check/build.py` still downloads into that checkout.

The model download is **8.06 GB**. Source/build caches and runtime dependencies need additional
disk space; reserve at least 30 GB, considerably more for a source build. No API keys needed.
[`runtime.lock.json`](runtime.lock.json) pins MAX 26.6.0 / Mojo 1.1.0 by tag and commit,
and Qwen3-4B by immutable revision and file hashes. Model/source downloads stay out of Git.

### Try the application without an AI runtime

```bash
python3 build.py --app-only
python3 run.py --demo
```

This explicit demo reads actual PDF text using limited regex rules. The UI labels it as a
**demo with no AI**; it is never a fallback for failed inference. Open an invoice, compare
its PDF, edit details if needed, approve/hold, and export.
A scan deliberately requires manual review. No fixture answers are used by the demo reader.

### Build MAX from its source

```bash
python3 build.py --inference-only --max-from-source --jobs 4
python3 run.py
```

This invokes the upstream Bazel target `//max/python/max/_entrypoints:pipelines` with
`--config=prebuilt-mojo`: MAX's public serving code and Mojo kernels are built using the
vendor's compiler toolchain. It does **not** bootstrap the entire compiler. The default
package path avoids this expensive developer build. In both paths, the model graph is
compiled at first startup; allow several minutes.

**Observed source-build limitation:** the tested MAX tag's LLVM archive download failed
its upstream checksum in this environment. Checksums remain enabled. Use the default official
package path, or retry the source build after the upstream artifact issue is resolved.
A failed build never writes the runtime-ready marker or silently switches runtime modes.

## Running and session storage

`run.py` checks ports, starts services in order, waits for readiness, and shuts down all child
process groups on Ctrl+C or a service failure. Logs are local under `inference/logs/`.

```bash
python3 run.py --frontend-port 3001 --backend-port 8081 --inference-port 8001
python3 run.py --device cpu             # slower; float32 needs substantially more system RAM
python3 build.py --download-only        # only the fixed source + model snapshot
```

SQLite is embedded through Rust's `rusqlite` with `open_in_memory()`: keys are invoice UUIDs,
values are JSON records, with PDF bytes stored alongside them. There is no Redis process,
database file or setup. **Edits and decisions survive page reloads, but disappear when the
Rust process stops.** Clear session removes everything immediately; stale AI jobs cannot
restore deleted invoices. Export approved records before shutdown. Model files are not cleared.

A correction reruns deterministic checks, records a required note and invalidates the previous
approval. Approving a flagged invoice requires an explanation. The app supports one local
user and up to 100 invoices / 200 MB per session; each upload is at most 20 MB and each checked
PDF at most five pages. The combined upload request is limited to 40 MB.

## Real local agents

Qwen is called over MAX's OpenAI-compatible local HTTP endpoint: `invoice_reader` transcribes,
`evidence_verifier` checks the transcription, and the read-only **Invoice assistant** answers
questions from the floating chat in the UI. Rust validates JSON and runs arithmetic, grounding,
PO matching, duplicate and receipt checks. Verifier concerns only add review requirements.
Failures never authorize payment or trigger a cloud fallback.

The assistant also accepts general questions with no invoice selected. Selecting an invoice is
optional and adds a server-built evidence catalog containing extracted fields, deterministic
findings, matched purchase-order data and current review status. The browser sends the optional
invoice ID/revision, question and up to three completed conversation pairs; it never supplies the
invoice evidence. Rust validates every evidence reference returned by the model and rejects stale
revisions or unsupported citations.

Chat is unavailable only in demo mode or when MAX is offline. If the inference worker is already
reading an invoice, the question waits for that request to finish and then takes priority over
queued invoice jobs. Its conversation stays in the browser tab and is not an approval, audit
entry, email or payment.

Other AI assistants can discover and call these agents through the Rust binary:

```bash
ai-backend/target/release/invoice-check agents list
ai-backend/target/release/invoice-check agents doctor
ai-backend/target/release/invoice-check agents call check_invoice --input-file examples/check-invoice.json
```

See [AGENTS.md](AGENTS.md) and the [JSON tool protocol](docs/AGENTS.md). The CLI does not change
UI decisions. It uses reference payment history; the UI additionally checks its session queue.

## Verify and develop

```bash
cargo fmt --manifest-path ai-backend/Cargo.toml --check
cargo clippy --manifest-path ai-backend/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path ai-backend/Cargo.toml --locked
npm ci --prefix frontend
npm run build --prefix frontend
cargo run --manifest-path ai-backend/Cargo.toml --locked -- eval

# With MAX running: measures actual model behavior, not fixture/oracle extraction.
ai-backend/target/release/invoice-check eval --live
```

For development use `cargo run --manifest-path ai-backend/Cargo.toml -- serve --demo` and,
in another terminal, `npm run dev --prefix frontend`. Vite proxies `/api` to Rust on 8080.
Remove `--demo` to use MAX, which must already be running. `run.py` serves the compiled React build.

The Rust suite covers 17 expected fixture decisions, all 49 injected extraction mistakes,
HTTP workflow, memory reset/revision conflicts, CSV escaping, and agent contracts with a fake
HTTP server. Those results do **not** measure Qwen accuracy, GPU performance or memory usage.
Actual MAX/Qwen evaluation: **10/17 expected decisions, zero unsafe approvals, seven clean
invoices unnecessarily flagged**. The verifier is overly conservative on formatting; one
reader output failed numeric validation. See [recorded evidence](evals/results/summary.json)
and [full live traces](evals/results/live-max.json). This is a development fixture result,
not a held-out benchmark or a promise of production accuracy.

Real invoices, OCR, multiple POs, credit notes, non-USD currencies and multi-user deployment
remain outside the slice. Reference ERP data and PDFs are synthetic fixtures; the database,
editing, checking, PDF preview, agent HTTP calls and export are implemented.

See [DESIGN.md](DESIGN.md) for thesis, architecture and tradeoffs. A root [README.mdx](README.mdx)
provides an MDX-compatible quickstart; this Markdown file is the Git repository's landing page.
