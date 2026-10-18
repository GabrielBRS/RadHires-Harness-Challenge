import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Layers, Download, Trash2, X } from "lucide-react";
import { api, type Record } from "./api";
import { Inbox } from "./features/inbox/Inbox";
import { Review } from "./features/review/Review";
import { InvoiceChat } from "./features/chat/InvoiceChat";
import "./style.css";
function App() {
  const [records, setRecords] = useState<Record[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [mode, setMode] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewEditing, setReviewEditing] = useState(false);
  const refresh = useCallback(async () => {
    const rows = await api<Record[]>("/invoices");
    setRecords(rows);
  }, []);
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const [rows, health] = await Promise.all([
          api<Record[]>("/invoices"),
          api<{ mode: string }>("/health"),
        ]);
        if (active) {
          setRecords(rows);
          setMode(health.mode);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  const action = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const current = records.find((r) => r.id === selected);
  return (
    <>
      <header className="app-header">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setSelected(null);
          }}
        >
          <span>
            <Layers size={21} />
          </span>
          invoice<span className="brand-light">check</span>
        </a>
        <div className="company">
          LUMEN SUPPLY CO. <span>Accounts payable workspace</span>
        </div>
        <div className="header-actions">
          <a className="button compact" href="/api/export.csv">
            <Download size={15} />
            Export approved
          </a>
          <button
            className="icon-button"
            aria-label="Clear session"
            title="Clear session"
            disabled={busy || !records.length}
            onClick={() => {
              if (
                confirm(
                  "Clear every invoice, edit and decision in this session? Export approved invoices first.",
                )
              )
                void action(async () => {
                  await api("/invoices", { method: "DELETE" });
                  setSelected(null);
                });
            }}
          >
            <Trash2 size={17} />
          </button>
          <span className="avatar">AP</span>
        </div>
      </header>
      {mode === "demo" && (
        <div className="demo-banner">
          DEMO MODE · Reads PDF text with basic rules. No AI is running; results
          may need more corrections.
        </div>
      )}
      <main>
        {error && (
          <div role="alert" className="error-banner">
            <span>{error}</span>
            <button
              className="icon-button"
              aria-label="Dismiss error"
              onClick={() => setError("")}
            >
              <X size={17} />
            </button>
          </div>
        )}
        {current ? (
          <Review
            record={current}
            onBack={() => setSelected(null)}
            onChange={(r) =>
              setRecords((rows) =>
                rows.map((old) => (old.id === r.id ? r : old)),
              )
            }
            onError={setError}
            onEditingChange={setReviewEditing}
            onDelete={() =>
              void action(async () => {
                await api(`/invoices/${current.id}`, { method: "DELETE" });
                setSelected(null);
              })
            }
          />
        ) : (
          <Inbox
            records={records}
            busy={busy}
            onOpen={setSelected}
            onSamples={() =>
              void action(async () => {
                await api("/samples", { method: "POST" });
              })
            }
            onUpload={(files) => {
              if (files?.length) {
                const form = new FormData();
                Array.from(files).forEach((file) => form.append("files", file));
                void action(async () => {
                  await api("/invoices", { method: "POST", body: form });
                });
              }
            }}
          />
        )}
      </main>
      <InvoiceChat
        records={records}
        activeId={selected}
        mode={mode}
        editing={reviewEditing}
      />
      <footer>
        Invoice Check <span>Built for the detail that matters.</span>
      </footer>
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
