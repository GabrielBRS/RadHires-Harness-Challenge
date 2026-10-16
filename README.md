# Invoice Check

**A second pair of eyes for the accounts-payable clerk.** Drop in supplier invoice PDFs.
Each one is checked against its purchase order, what the warehouse actually received,
and what has already been paid. Then it is sorted into **Ready to approve**, **Needs a look**
or **Don't pay yet**, with a plain-English reason, the amount at stake, and a drafted
email to the supplier when something's off.

It's built for the AP person at a small or mid-size distributor: someone who works through
a pile of PDFs from dozens of suppliers every week, each with its own layout, and checks
them by hand against ERP screens.

> Design rationale, tradeoffs and limitations are in [DESIGN.md](DESIGN.md).

---

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then put your key in it (or export ANTHROPIC_API_KEY=...)
python -m app.web             # open http://localhost:5000
```

Click **Load the 17 sample invoices**, then open any row.

**No API key?** The app still runs. It falls back to a basic regex reader and says so in the
footer. That reader only understands simple table layouts, so more invoices land in
"Needs a look". It never approves on a guess (see the evals).

### Command line

```bash
python -m harness data/samples/03_halvorsen_price_up.pdf          # one or more PDFs
python -m harness --json data/samples/14_halvorsen_two_problems.pdf
```

### Evals and tests

```bash
python evals/run_eval.py --extractor oracle     # business rules alone, given perfect reading
python evals/run_eval.py --extractor baseline   # regex reader, no key
python evals/run_eval.py --extractor llm        # Claude end to end (saves its extractions)
python evals/run_eval.py --extractor replay     # re-score the saved LLM run, no API calls
python evals/fault_injection.py                 # corrupt the reading 7 ways: do we fail closed?
python -m unittest discover tests
```

Reports are written to `evals/results/`. The eval exits non-zero if any invoice with a real
problem gets marked "ready to approve".

### Regenerate the sample data

```bash
python data/generate.py
```

---

## What's in the box

```
app/          Flask web UI (inbox, side-by-side review, decisions, supplier email, CSV export)
harness/
  extract.py    PDF text -> structured invoice (Claude via forced tool call; regex baseline)
  grounding.py  every extracted value must appear in the PDF text
  checks.py     arithmetic, PO lookup, supplier, duplicates, line matching, price, quantity
  pipeline.py   orchestration + decision (approve / review / hold)
  explain.py    supplier query email (template, from the checks' findings only)
  config.py     model settings + AP tolerances in one readable place
data/         synthetic POs, goods receipts, payment history, 17 invoice PDFs + ground truth
evals/        decision/issue/field scoring, fault injection
tests/        unit tests (incl. LLM contract tests against a fake API)
```

## Real vs. stubbed

| Part | Status |
|---|---|
| PDF reading, LLM extraction, grounding, all checks, decision, UI, email draft, evals | **Real** |
| Purchase orders, goods receipts, payment history | **Stubbed**: JSON files standing in for the ERP |
| Invoices | **Synthetic**: 17 PDFs in 3 supplier layouts, each discrepancy planted on purpose |
| ERP posting | **Stubbed**: "Download approved (CSV)" stands in for an ERP import |
| Sending email | **Stubbed**: the draft is copied to the clipboard; the clerk sends it from their own mail |
| Users / auth / audit trail | Minimal: one user, decisions + notes logged per invoice in `data/runtime/state.json` |

## Known limitations

- **Scanned or faxed invoices are detected but not read** (sent to "Needs a look" with a clear
  message). Adding OCR or sending the page image to the model is the obvious next step.
- **Single currency (USD), one tax rate, one PO per invoice.** Credit notes and invoices that span
  several POs aren't handled.
- **The sample set is small (17) and I wrote it.** It shows the checks do what I intended. It is
  not evidence about real-world supplier invoices. See DESIGN.md → *Reflections*.
- The queue is a JSON file and a single worker thread. That's fine for one clerk, not for a team.
