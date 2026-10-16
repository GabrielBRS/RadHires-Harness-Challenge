# Invoice Check: design notes

## 1. The problem

**Who.** The accounts-payable specialist at a small or mid-size distributor, often a one- or
two-person team. At our fictional Lumen Supply Co. (building supplies, 3 branches) that means a
few hundred supplier invoices a week. Most arrive as PDFs by email from small suppliers who don't
do EDI, and every supplier lays its invoice out differently.

**The job.** Before paying, AP does a *three-way match*: does the invoice agree with the purchase
order (what we agreed to buy, at what price) and with the goods receipt (what actually arrived)?
And is it a duplicate of something already paid? Today that means opening the PDF, finding the PO
in the ERP, comparing line by line, and checking the payment history.

**What it costs.** I don't have real figures, so these are *illustrative assumptions*, not data:
at ~250 invoices/week and a few minutes each, that's a day or more of skilled time every week
spent mostly confirming that clean invoices are clean. The bigger cost is what slips through when
someone is rushing: a price quietly raised by $1.50 a box, a supplier billing before the goods
arrived, the same invoice re-sent with a new number. These are small, frequent leaks that nobody
notices.

**Why an AI harness fits, and where it doesn't.**
- *Fits:* the hard, tedious part is **reading**. Labels differ ("PO Number" vs "Your Order Ref"),
  references come in different formats ("PO-45000120" vs "4500-0120"), and one supplier types its
  invoice as a letter. Regexes and templates break on exactly this variety. A model handles it well.
- *Doesn't fit:* **deciding whether to pay.** That's company policy, it has to be auditable, and it
  must be exactly repeatable. So the model never decides anything. It transcribes. Plain code does
  the matching and the judgement, and a person approves.
- *Not for:* companies already on EDI or e-invoicing (the data is structured already), and scanned
  faxes (detected but not read in this slice).

**Scope.** One workflow, end to end: *PDF invoice → checked → decision by the clerk → approved
batch out*. Everything around it (ERP, email sending, auth) is stubbed.

## 2. The user and the interface

The clerk thinks in **POs, receipts, credit notes and holds**, not in "extraction confidence".
They want two things: to clear the clean invoices fast, and to be told exactly what's wrong
with the rest.

**Why a web app.** The clerk already works at a desktop, with PDFs in email and the ERP in a
browser. The work is *batch* (a pile every morning) and *visual* (you have to see the invoice next to
the findings). I considered:
- a **forward-to-an-email-address bot**: the lowest-friction intake, but reviewing a flag needs
  a side-by-side view, so it would end up linking to a web page anyway;
- a **chat surface** (Slack/Teams): wrong shape for a queue of 50 items that needs an audit trail;
- an **ERP plugin**: the right home long-term, but far too much integration for a slice.

**The experience.**
1. **Drop the pile** onto the page. Invoices are read in the background and
   the page updates as they come in.
2. **A triaged queue.** *Don't pay yet* first, then *Needs a look*, then *Ready to approve*. Each
   row shows a one-line reason ("Charged more than the agreed price · $15.00 in question"). Status
   is shown with words and icons, never colour alone.
3. **Clear the clean ones in one click** ("Approve all 7 ready", with a confirmation).
4. **Review side by side.** The invoice page sits on the left. On the right: what's wrong, with
   the numbers, and *what to do about it*; what was read; and how each line compares with the PO
   ("PO price $34.90 · received 10 · matched by item code"). Any value that couldn't be confirmed
   on the page is **highlighted yellow with "check this"**.
5. **Act.** *Approve for payment*, *Put on hold*, or *Email the supplier*. The email is a draft
   built only from the findings, which the clerk edits and copies. **Approving a flagged invoice
   requires a short note** ("Purchasing confirmed new price"). That's friction exactly where it
   matters, and it doubles as the audit trail.
6. **Out.** Download the approved batch as CSV for the ERP import.

**How they know it worked:** a green banner that names the PO and the amount ("Matches
PO-45000110 and what the warehouse received. OK to pay $1,001.79.").

**What happens when it doesn't:** the clerk never sees a stack trace or a silent failure. A
scanned invoice says "Couldn't read this PDF, it looks like a scan" and suggests a next step. With
no API key, the footer says it's in demo mode with a basic reader.

## 3. Architecture

```
PDF ─► text layer ─► EXTRACT ─► GROUND ─► ARITHMETIC ─► PO + SUPPLIER ─► DUPLICATES ─► LINE MATCH ─► DECIDE
      (pdfplumber)   (Claude,    every     supplier's    key lookup,      paid history   item code,     approve /
       no text? ──►  forced      value     own maths     fuzzy name       + this queue,  then fuzzy     review /
       UNREADABLE    tool call)  on page?                                 exact + fuzzy  description;   hold
                                                                                         price & qty
                                                                                         vs receipts
```

**Key decisions**

- **The model only transcribes.** It works through a forced `record_invoice` tool call with a
  strict schema at temperature 0. The prompt says *copy, never calculate or correct*. That matters:
  if the model quietly "fixed" a supplier's bad arithmetic, we'd lose the very signal we're
  checking for. If the output fails schema validation, the error goes back to the model once and
  then the invoice falls back to manual entry.
- **Grounding check, the cheap hallucination guard.** Every extracted number, code and date must
  appear in the PDF's text. Unconfirmed values are flagged to the clerk. An unconfirmed PO number
  is not used at all, so the invoice can't be matched to the wrong PO.
- **Business rules are plain code.** Tolerances live in `config.py` where a finance lead can read
  them: 0.5% price drift, 2¢ rounding, a 14-day duplicate window. Every flag carries the rule, the
  numbers and the amount at stake.
- **Fail closed.** "Ready to approve" requires positive evidence on every check. Anything missing,
  unreadable, unconfirmed or unmatched lands in review or hold. Nothing is ever paid
  automatically: "approve" means "ready for *you* to approve".
- **Partial billing and duplicates are handled.** Quantity is checked against
  *received minus already paid*. Duplicates are caught both exactly (same supplier and number) and
  fuzzily (same supplier and amount within 14 days under a new number), against payment history
  and the current queue.

**How I know if it's any good.** There are three evals, each answering a different question:

| Eval | Question | Result |
|---|---|---|
| `oracle` (perfect reading) | Do the business rules give the right answer? | 17/17 decisions, 0 unsafe approvals |
| `baseline` (regex reader) | How much does the model earn its place? | 53% decisions, 62% field accuracy, **0 unsafe approvals** |
| `fault_injection` | If the reader makes a mistake, do we still fail closed? | **49/49** corrupted readings not marked ready |
| `llm` (Claude end to end) | The real thing | *Run `python evals/run_eval.py --extractor llm` and paste results here* |

The headline metric is **unsafe approvals**: problem invoices marked "ready". That's money out the
door. The second metric is **needless flags**: clean invoices sent to review, which costs the clerk
a minute each. The fault-injection test corrupts a perfect reading in seven realistic ways
(misread price, a "self-consistent" misread where the model also fixes the totals, a dropped line,
an invented line, a hallucinated PO, a wrong total, a wrong quantity). It confirms that the safety
net works independently of how good the model is. Saved LLM extractions can be re-scored for free
(`--extractor replay`) when the rules change.

## 4. Tooling and tradeoffs

| Choice | Why | Weighed against |
|---|---|---|
| Python + Flask + server-rendered HTML | No build step; one `pip install`; full control over the clerk's screen | Streamlit is faster to build, but its rerun model feels less like a work queue. React is overkill for two screens. |
| Claude via forced tool use, raw HTTP | The schema is enforced by the API; one dependency fewer; the request is fully visible | Official SDK (equally good; swapping is trivial) |
| Sonnet by default, configurable | Quality first on messy layouts | Haiku is probably enough for transcription and much cheaper. The `llm` eval is how I'd decide. |
| PDF **text layer** → model | Makes grounding possible; cheap; fast | Sending the PDF/page image to the model reads scans too, but loses the grounding check (text to verify against) |
| Deterministic fuzzy matching | Auditable, repeatable, explainable | An LLM matcher would handle wilder descriptions, at the cost of explainability |
| Template supplier email | Goes to an outside company: must contain only verified facts | An LLM-written email reads nicer but could state things the checks didn't establish |
| JSON files for ERP + queue | Honest stub for a vertical slice | SQLite or a real ERP API: next step, not needed to prove the workflow |

**Deliberately not used:** agent frameworks (the control flow is a straight line, and an agent loop
adds nothing but unpredictability), RAG or vector stores (finding a PO is a key lookup), and any
auto-pay path.

## 5. Reflections

**Time spent:** *[fill in honestly, e.g. "~X hours: Y on problem/data, Z on harness, W on UI, V on evals and docs"]*

**What I'd do next**
1. **Real invoices.** Get 50-100 anonymised invoices from real small suppliers (not from any employer)
   and run the `llm` eval. That is the number I trust least today.
2. **Scans.** Send image-only pages to the model's vision input, and replace text grounding with a
   second independent read plus agreement checking.
3. **Learn from overrides.** The clerk's notes on flagged-but-approved invoices are labelled data.
   "Approved because purchasing agreed new price" repeated 10 times for one supplier suggests the PO
   price, not the invoice, is wrong.
4. **ERP integration**, credit notes, multi-PO invoices, per-supplier tolerances, multi-currency.
5. **Tracing** of every extraction (prompt, output, grounding result, clerk's final decision) to
   monitor drift when a supplier changes its layout.

**What I'm least sure about**
- **The eval set is mine.** I wrote the invoices *and* the rules, so oracle's 100% is close to
  circular. It proves the rules do what I meant, not that what I meant covers reality. The fault
  injection and an LLM run on real invoices are the honest signals.
- **Grounding can be fooled** when a misread number happens to appear elsewhere on the page (a
  quantity of "7" when the tax line says "7%"). The arithmetic and PO checks usually catch it
  downstream, but not always.
- **Tolerances are guesses.** 0.5% and 14 days should come from the company's controller, not
  from me.
- **"Approve all ready" is a deliberate bet.** It saves the most time, and it leans entirely on the
  safety net. In production I'd watch how often clerks reverse a bulk approval before trusting it.
