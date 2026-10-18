# Local tools for other AI agents

The Rust binary exposes JSON stdin/stdout tools. It is usable by a coding assistant or
any agent runner with a shell tool; no IDE-specific plugin or cloud API is required.
The HTTP UI API also exposes read-only tool discovery at `GET /api/agents`.

Start `python3 run.py` first. From the repository root:

```bash
ai-backend/target/release/invoice-check agents list
ai-backend/target/release/invoice-check agents doctor
ai-backend/target/release/invoice-check agents call check_invoice --input-file examples/check-invoice.json
```

Alternatively pipe a JSON object to stdin, or use `--root /absolute/project/path` **before**
`agents` when calling from another directory. On Windows execute these inside WSL.

- `read_invoice`: input `{ "pdf_path": "data/samples/01_halvorsen_clean.pdf" }`.
  Calls the Qwen reader. Returns an **unverified** extraction.
- `verify_invoice`: same input plus `extraction`, an object conforming to the discovery schema.
  Calls the evidence verifier; returns `concerns`. This does not run business rules.
- `check_invoice`: same input as read_invoice. Reads, verifies and runs business checks.
  Returns extraction, issues, matched PO, recommendation and two agent execution traces.

Every command emits one JSON object with `ok` and `data`, or `ok: false` and `error`.
`check_invoice` reports failed roles in `data.agent_runs` and exits 2. An unreadable PDF
also exits 2. A legitimate review/hold recommendation after successful inference is not
an infrastructure failure. Diagnostics go to stderr.

The CLI is read-only: it does not enqueue documents, approve, export, send email or pay.
Its duplicate checks use the synthetic payment history; it cannot see the UI's in-memory
queue. The UI adds current-session duplicate checking. The schema is available through
`agents list`; use it rather than inventing fields. Files are limited to 20 MB / five pages.

If `doctor` fails, follow [LOCAL_AI.md](LOCAL_AI.md). Do not select `--demo`, substitute
fixture answers, or say the model ran. Only download/build when authorized to set up the project.
