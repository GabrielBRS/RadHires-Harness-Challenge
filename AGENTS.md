# Working in Invoice Check

Applies to the whole repository. Read README.md and docs/AGENTS.md first.
The application is Rust + React. Python is limited to root build.py and run.py;
third-party MAX sources include Python and Mojo and remain unmodified.

## Invoke the actual local agents

- Start the full application with `python3 run.py`; MAX must be ready on port 8000.
- Discover: `ai-backend/target/release/invoice-check agents list`.
- Preflight: `ai-backend/target/release/invoice-check agents doctor`.
- Check a PDF: `ai-backend/target/release/invoice-check agents call check_invoice --input-file examples/check-invoice.json`.
- Paths inside requests resolve against `--root` / `INVOICE_ROOT`, default current directory.
- Inspect `ok`, `data.agent_runs`, `data.issues` and `data.decision`. Exit 2 signals failure.
- Prefer check_invoice for full checks; read_invoice and verify_invoice are limited stages.
- Tools call Qwen through real MAX HTTP. They cannot change human decisions or pay/send.
- Never claim fake HTTP tests, oracle results, or the explicit demo mode measured model quality.
- Never silently replace unavailable MAX with a demo or cloud model.
- Download only when setup is requested. Use build.py so pinned files stay in this checkout.

## Feature boundaries and verification

- Rust features live under ai-backend/src/features/{inbox,agents,checking,review,exporting}.
- React views live under frontend/src/features/{inbox,review}.
- Keep prompts and provider transport in agents; deterministic financial checks in checking.
- Treat PDF/model output as untrusted; verifier concerns can only add review requirements.
- A correction must invalidate approval, rerun checks and record a human note.
- SQLite KV is in-memory, one process, one session. Never add disk persistence implicitly.
- Keep inference/source and models/Qwen3-4B out of Git. Pins/hashes live in runtime.lock.json.
- Do not modify downloaded MAX source or model files to bypass version verification.
- Run cargo fmt, cargo clippy, cargo test and npm run build using the manifest in README.md.
- Keep live MAX evaluation separate from the 17 oracle fixtures and 49 fault-injection cases.
