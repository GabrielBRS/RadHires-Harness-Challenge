import { useState } from "react";
import {
  ArrowUpRight,
  Search,
  FileText,
  Upload,
  LoaderCircle,
} from "lucide-react";
import { type Record, money, label, tone } from "../../api";
type Props = {
  records: Record[];
  onOpen: (id: string) => void;
  onUpload: (files: FileList | null) => void;
  onSamples: () => void;
  busy: boolean;
};
export function Inbox({ records, onOpen, onUpload, onSamples, busy }: Props) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const shown = records.filter((r) => {
    const s =
      `${r.filename} ${r.result?.extraction?.supplier_name} ${r.result?.extraction?.invoice_number}`.toLowerCase();
    return (
      s.includes(search.toLowerCase()) &&
      (filter === "all" ||
        (filter === "approved"
          ? r.human_decision === "approved"
          : filter === "ready"
            ? r.result?.decision === "approve" && !r.human_decision
            : !r.human_decision && r.result?.decision !== "approve"))
    );
  });
  return (
    <>
      <section className="page-title">
        <div>
          <p className="eyebrow">ACCOUNTS PAYABLE</p>
          <h1>A clearer path to payment.</h1>
          <p>Check the details. Resolve exceptions. Approve with confidence.</p>
        </div>
        <label className={`button primary ${busy ? "disabled" : ""}`}>
          <Upload size={17} /> Upload invoices
          <input
            aria-label="Upload invoices"
            type="file"
            accept="application/pdf"
            multiple
            disabled={busy}
            onChange={(e) => {
              onUpload(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      </section>
      <section className="metrics">
        <div>
          <span>IN THIS SESSION</span>
          <strong>
            {records.length}
            <small> invoices</small>
          </strong>
        </div>
        <div>
          <span>NEEDS ATTENTION</span>
          <strong>
            {
              records.filter(
                (r) =>
                  r.status === "done" &&
                  r.result?.decision !== "approve" &&
                  !r.human_decision,
              ).length
            }
            <small> to resolve</small>
          </strong>
        </div>
        <div>
          <span>APPROVED FOR EXPORT</span>
          <strong>
            {money(
              records
                .filter((r) => r.human_decision === "approved")
                .reduce(
                  (sum, r) => sum + (r.result?.extraction?.total || 0),
                  0,
                ),
            )}
          </strong>
        </div>
      </section>
      <section className="queue">
        <div className="queue-tools">
          <div className="tabs">
            {[
              ["all", "All invoices"],
              ["attention", "Needs attention"],
              ["ready", "Ready"],
              ["approved", "Approved"],
            ].map(([key, title]) => (
              <button
                key={key}
                className={filter === key ? "selected" : ""}
                onClick={() => setFilter(key)}
              >
                {title}
              </button>
            ))}
          </div>
          <label className="search">
            <Search size={16} />
            <input
              placeholder="Search invoices…"
              aria-label="Search invoices"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
        {!records.length ? (
          <div className="empty">
            <div className="empty-icon">
              <FileText size={32} />
            </div>
            <h2>Your next payment run starts here.</h2>
            <p>
              Add supplier PDFs to compare them with purchase orders,
              <br />
              goods received and payment history.
            </p>
            <button className="button" onClick={onSamples} disabled={busy}>
              Explore 17 sample invoices <ArrowUpRight size={16} />
            </button>
            <small>Synthetic examples · nothing is sent or paid</small>
          </div>
        ) : !shown.length ? (
          <div className="empty">
            <h2>No invoices match this view.</h2>
            <button
              className="text-button"
              onClick={() => {
                setFilter("all");
                setSearch("");
              }}
            >
              Show all invoices
            </button>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Supplier / invoice</th>
                  <th>Purchase order</th>
                  <th>Amount</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id} onClick={() => onOpen(r.id)}>
                    <td>
                      <button
                        className="invoice-link"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpen(r.id);
                        }}
                      >
                        {r.result?.extraction?.supplier_name || r.filename}
                      </button>
                      <small>
                        {r.result?.extraction?.invoice_number || r.filename}
                      </small>
                    </td>
                    <td>{r.result?.extraction?.po_number || "—"}</td>
                    <td className="amount">
                      {money(r.result?.extraction?.total)}
                    </td>
                    <td>
                      <span className={`badge ${tone(r)}`}>
                        {r.status !== "done" && (
                          <LoaderCircle className="spin" size={13} />
                        )}
                        <i />
                        {label(r)}
                      </span>
                    </td>
                    <td>
                      <ArrowUpRight size={17} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="queue-footer">
          <span>{shown.length} invoices in this view</span>
          <span>Every approval stays in your hands</span>
        </div>
      </section>
      <p className="session-note">
        Session workspace · Your invoices and edits stay available while the
        backend is running. Export before closing it.
      </p>
    </>
  );
}
