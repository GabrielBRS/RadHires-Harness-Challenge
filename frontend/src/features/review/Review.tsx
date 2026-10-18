import { lazy, Suspense, useEffect, useState } from "react";
const PdfPreview = lazy(() =>
  import("./PdfPreview").then((module) => ({ default: module.PdfPreview })),
);
import {
  ArrowLeft,
  Check,
  Pencil,
  Plus,
  Trash2,
  X,
  Mail,
  Download,
  ShieldCheck,
} from "lucide-react";
import {
  api,
  json,
  type Invoice,
  type Record,
  money,
  label,
  tone,
} from "../../api";
const blank: Invoice = {
  supplier_name: null,
  invoice_number: null,
  invoice_date: null,
  po_number: null,
  currency: "USD",
  subtotal: null,
  tax: null,
  total: null,
  lines: [],
};
type Props = {
  record: Record;
  onBack: () => void;
  onChange: (r: Record) => void;
  onDelete: () => void;
  onError: (s: string) => void;
  onEditingChange: (editing: boolean) => void;
};
export function Review({
  record: r,
  onBack,
  onChange,
  onDelete,
  onError,
  onEditingChange,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [editRevision, setEditRevision] = useState(r.revision);
  const [form, setForm] = useState<Invoice>(r.result?.extraction || blank);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<{ subject: string; body: string } | null>(
    null,
  );
  useEffect(() => {
    setEditing(false);
    setNote("");
    setDraft(null);
  }, [r.id]);
  useEffect(() => {
    onEditingChange(editing);
    return () => onEditingChange(false);
  }, [editing, onEditingChange]);
  const mutate = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      onError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };
  const decision = (decision: string) =>
    mutate(async () => {
      onChange(
        await api<Record>(
          `/invoices/${r.id}/decision`,
          json("POST", { revision: r.revision, decision, note }),
        ),
      );
      setNote("");
    });
  const save = () =>
    mutate(async () => {
      onChange(
        await api<Record>(
          `/invoices/${r.id}`,
          json("PATCH", { revision: editRevision, extraction: form, note }),
        ),
      );
      setEditing(false);
      setNote("");
    });
  const ex = editing ? form : r.result?.extraction;
  const issues = r.result?.issues || [];
  const field = (
    key: keyof Omit<Invoice, "lines">,
    title: string,
    numeric = false,
  ) => (
    <label key={key}>
      {title}
      {editing ? (
        <input
          aria-label={title}
          type={numeric ? "number" : key === "invoice_date" ? "date" : "text"}
          step={numeric ? "any" : undefined}
          value={form[key] ?? ""}
          onChange={(e) =>
            setForm({
              ...form,
              [key]: numeric
                ? e.target.value === ""
                  ? null
                  : Number(e.target.value)
                : e.target.value || null,
            })
          }
        />
      ) : (
        <strong>
          {numeric ? money(ex?.[key] as number | null) : ex?.[key] || "—"}
        </strong>
      )}
    </label>
  );
  return (
    <>
      <div className="detail-top">
        <button className="text-button" onClick={onBack}>
          <ArrowLeft size={16} /> All invoices
        </button>
        <span className={`badge ${tone(r)}`}>{label(r)}</span>
      </div>
      <div className="detail-title">
        <div>
          <h1>{r.result?.extraction?.supplier_name || "Review invoice"}</h1>
          <p>
            {r.result?.extraction?.invoice_number || r.filename}{" "}
            <span className="separator">/</span> {r.filename}
          </p>
        </div>
        <button
          className="icon-button danger"
          title="Remove this invoice"
          aria-label="Remove this invoice"
          onClick={() => {
            if (confirm("Remove this invoice and its session history?"))
              onDelete();
          }}
        >
          <Trash2 size={18} />
        </button>
      </div>
      <div className="review-layout">
        <section className="document">
          <div className="panel-heading">
            <h2>Original invoice</h2>
            <a
              href={`/api/invoices/${r.id}/pdf`}
              target="_blank"
              rel="noreferrer"
              aria-label="Open original PDF"
            >
              <Download size={18} />
            </a>
          </div>
          <Suspense fallback={<p className="muted">Loading PDF viewer…</p>}>
            <PdfPreview key={r.id} id={r.id} />
          </Suspense>
        </section>
        <section className="review-panel">
          {r.status !== "done" ? (
            <div className="empty">
              <h2>Reading your invoice…</h2>
              <p>We’ll compare the details as soon as the reader finishes.</p>
            </div>
          ) : (
            <>
              <div className="panel-heading">
                <h2>
                  {editing ? "Correct invoice details" : "Invoice details"}
                </h2>
                <button
                  className="text-button"
                  onClick={() => {
                    setForm(structuredClone(r.result?.extraction || blank));
                    setEditRevision(r.revision);
                    setEditing(!editing);
                    setNote("");
                  }}
                  disabled={busy}
                >
                  {editing ? (
                    <>
                      <X size={15} />
                      Cancel
                    </>
                  ) : (
                    <>
                      <Pencil size={15} />
                      Edit details
                    </>
                  )}
                </button>
              </div>
              <div className="fields">
                {field("supplier_name", "Supplier")}
                {field("invoice_number", "Invoice number")}
                {field("invoice_date", "Invoice date")}
                {field("po_number", "Purchase order")}
                {field("currency", "Currency")}
                {field("total", "Total due", true)}
                {editing && field("subtotal", "Subtotal", true)}
                {editing && field("tax", "Tax", true)}
              </div>
              <div className="line-items">
                <h3>Line items</h3>
                {ex?.lines.map((l, i) =>
                  editing ? (
                    <div className="line-editor" key={i}>
                      <input
                        aria-label={`Description ${i + 1}`}
                        value={l.description}
                        placeholder="Description"
                        onChange={(e) =>
                          setForm({
                            ...form,
                            lines: form.lines.map((l, j) =>
                              j === i
                                ? { ...l, description: e.target.value }
                                : l,
                            ),
                          })
                        }
                      />
                      <div>
                        {(
                          [
                            "sku",
                            "quantity",
                            "unit_price",
                            "line_total",
                          ] as const
                        ).map((k) => (
                          <label key={k}>
                            {
                              {
                                sku: "Item code",
                                quantity: "Quantity",
                                unit_price: "Unit price",
                                line_total: "Amount",
                              }[k]
                            }
                            <input
                              aria-label={`${k} ${i + 1}`}
                              type={k === "sku" ? "text" : "number"}
                              step="any"
                              value={l[k] ?? ""}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  lines: form.lines.map((l, j) =>
                                    j === i
                                      ? {
                                          ...l,
                                          [k]:
                                            k === "sku"
                                              ? e.target.value || null
                                              : e.target.value === ""
                                                ? k === "line_total"
                                                  ? null
                                                  : 0
                                                : Number(e.target.value),
                                        }
                                      : l,
                                  ),
                                })
                              }
                            />
                          </label>
                        ))}
                        <button
                          className="icon-button"
                          aria-label={`Remove line ${i + 1}`}
                          onClick={() =>
                            setForm({
                              ...form,
                              lines: form.lines.filter((_, j) => j !== i),
                            })
                          }
                        >
                          <X size={15} />
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="line-read" key={i}>
                      <div>
                        <strong>{l.description}</strong>
                        <small>
                          {l.sku || "No item code"} · {l.quantity} ×{" "}
                          {money(l.unit_price)}
                        </small>
                      </div>
                      <b>{money(l.line_total)}</b>
                    </div>
                  ),
                )}
                {!ex?.lines.length && (
                  <p className="muted">
                    No line items were read. Compare the PDF and enter details.
                  </p>
                )}
                {editing && (
                  <button
                    className="text-button"
                    onClick={() =>
                      setForm({
                        ...form,
                        lines: [
                          ...form.lines,
                          {
                            description: "",
                            sku: null,
                            quantity: 1,
                            unit_price: 0,
                            line_total: null,
                          },
                        ],
                      })
                    }
                  >
                    <Plus size={15} /> Add line item
                  </button>
                )}
              </div>
              {editing ? (
                <div className="actions">
                  <label>
                    What did you correct?
                    <textarea
                      aria-label="Correction note"
                      placeholder="Describe your correction (at least 5 characters)"
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                    />
                  </label>
                  <p className="muted">
                    Saving runs the checks again and clears any previous
                    approval.
                  </p>
                  <button
                    className="button primary"
                    disabled={busy || note.trim().length < 5}
                    onClick={save}
                  >
                    <Check size={16} />
                    Save and recheck
                  </button>
                </div>
              ) : (
                <>
                  <div className="findings">
                    <h3>What needs your attention</h3>
                    {issues.length ? (
                      issues.map((issue, i) => (
                        <article
                          className={`finding ${issue.severity}`}
                          key={i}
                        >
                          <span className="finding-mark">
                            {issue.severity === "info" ? "i" : "!"}
                          </span>
                          <div>
                            <h4>{issue.title}</h4>
                            <p>{issue.detail}</p>
                            {issue.severity !== "info" && (
                              <small>{issue.next_step}</small>
                            )}
                          </div>
                        </article>
                      ))
                    ) : (
                      <article className="finding clean">
                        <ShieldCheck size={21} />
                        <div>
                          <h4>The checks found no exceptions.</h4>
                          <p>
                            Compare the document, then approve when you’re
                            ready.
                          </p>
                        </div>
                      </article>
                    )}
                  </div>
                  {r.result?.po && (
                    <details>
                      <summary>
                        Matched purchase order · {r.result.po.po_number}
                      </summary>
                      {r.result.po.lines.map((l) => (
                        <div className="line-read" key={l.line}>
                          <div>
                            <strong>{l.description}</strong>
                            <small>
                              {l.sku} · {l.qty_received} received
                            </small>
                          </div>
                          <b>{money(l.unit_price)} ea</b>
                        </div>
                      ))}
                    </details>
                  )}
                  <div className="actions">
                    <label>
                      Review note
                      <textarea
                        aria-label="Review note"
                        placeholder={
                          r.result?.decision === "approve"
                            ? "Optional note for this decision"
                            : "Explain any approval of flagged findings (at least 5 characters)"
                        }
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                      />
                    </label>
                    {r.human_decision ? (
                      <>
                        <p>
                          Recorded: <strong>{label(r)}</strong>
                        </p>
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() => decision("undo")}
                        >
                          Undo decision
                        </button>
                      </>
                    ) : (
                      <div className="action-row">
                        <button
                          className="button primary"
                          disabled={busy || !ex}
                          onClick={() => decision("approved")}
                        >
                          <Check size={16} />
                          Approve
                        </button>
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() => decision("held")}
                        >
                          Hold
                        </button>
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() =>
                            mutate(async () =>
                              setDraft(await api(`/invoices/${r.id}/draft`)),
                            )
                          }
                        >
                          <Mail size={16} />
                          Supplier draft
                        </button>
                      </div>
                    )}
                    <small>
                      Approvals prepare the CSV. No payments or emails are sent.
                    </small>
                  </div>
                </>
              )}
              <details>
                <summary>Review history & reading details</summary>
                <p className="muted">
                  Current fields:{" "}
                  {r.result?.extractor === "manual"
                    ? "human correction; earlier AI traces are historical"
                    : r.result?.extractor === "demo"
                      ? "demo rules (no AI)"
                      : "local AI"}
                </p>
                {r.result?.agent_runs.map((a, i) => (
                  <p className="trace" key={i}>
                    {a.agent} · {a.status} · {a.seconds.toFixed(1)}s
                    {a.error && <small>{a.error}</small>}
                  </p>
                ))}
                {r.audit.map((a, i) => (
                  <p className="trace" key={i}>
                    <strong>{a.action}</strong> ·{" "}
                    {new Date(a.at).toLocaleString()}
                    <small>{a.note || "No note"}</small>
                  </p>
                ))}
              </details>
            </>
          )}
        </section>
      </div>
      {draft && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="draft-title"
          >
            <div className="panel-heading">
              <h2 id="draft-title">Supplier email draft</h2>
              <button
                className="icon-button"
                aria-label="Close draft"
                onClick={() => setDraft(null)}
              >
                <X />
              </button>
            </div>
            <label>
              Subject
              <input
                value={draft.subject}
                onChange={(e) =>
                  setDraft({ ...draft, subject: e.target.value })
                }
              />
            </label>
            <label>
              Message
              <textarea
                rows={13}
                value={draft.body}
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
              />
            </label>
            <p className="muted">
              Check the wording, copy it, and send it through your email app.
            </p>
            <div className="action-row">
              <button
                className="button"
                onClick={() =>
                  mutate(() =>
                    navigator.clipboard.writeText(
                      `Subject: ${draft.subject}\n\n${draft.body}`,
                    ),
                  )
                }
              >
                Copy draft
              </button>
              <button
                className="button primary"
                disabled={busy}
                onClick={() => {
                  void decision("queried");
                  setDraft(null);
                }}
              >
                I contacted the supplier
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
