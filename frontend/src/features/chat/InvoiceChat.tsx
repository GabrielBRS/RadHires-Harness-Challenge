import {
  Bot,
  ChevronDown,
  LoaderCircle,
  MessageCircle,
  RotateCcw,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  api,
  json,
  type ChatAnswer,
  type ChatEvidence,
  type ChatTurn,
  type Record as InvoiceRecord,
} from "../../api";

type Props = {
  records: InvoiceRecord[];
  activeId: string | null;
  mode: string;
  editing: boolean;
};

type Message = ChatTurn & {
  id: string;
  evidence?: ChatEvidence[];
  showInvoiceHint?: boolean;
  pending?: boolean;
  failed?: boolean;
};

const invoiceSuggestions = [
  "Why does this invoice need review?",
  "What differs from the purchase order?",
  "Summarize the totals and line items.",
];

const contextLabel = (record: InvoiceRecord) => {
  const supplier = record.result?.extraction?.supplier_name;
  const number = record.result?.extraction?.invoice_number;
  return [supplier, number].filter(Boolean).join(" · ") || record.filename;
};

const takeCharacters = (value: string, limit: number) =>
  Array.from(value).slice(0, limit).join("");

const historyFor = (messages: Message[]): ChatTurn[] => {
  const completed = messages.filter(
    (message) => !message.failed && !message.pending,
  );
  const history: ChatTurn[] = [];
  let characters = 0;

  for (let end = completed.length; end >= 2 && history.length < 6; end -= 2) {
    const user = completed[end - 2];
    const assistant = completed[end - 1];
    if (user.role !== "user" || assistant.role !== "assistant") continue;
    const pair: ChatTurn[] = [
      { role: "user", content: takeCharacters(user.content, 1000) },
      { role: "assistant", content: takeCharacters(assistant.content, 3000) },
    ];
    const pairCharacters = pair.reduce(
      (total, turn) => total + Array.from(turn.content).length,
      0,
    );
    if (characters + pairCharacters > 4000) break;
    history.unshift(...pair);
    characters += pairCharacters;
  }

  return history;
};

export function InvoiceChat({ records, activeId, mode, editing }: Props) {
  const [open, setOpen] = useState(false);
  const [contextChoice, setContextChoice] = useState("auto");
  const [threads, setThreads] = useState<globalThis.Record<string, Message[]>>(
    {},
  );
  const [question, setQuestion] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  const completed = useMemo(
    () => records.filter((record) => record.status === "done"),
    [records],
  );
  const selectedContextId =
    contextChoice === "auto" ? activeId || "" : contextChoice;
  const selectedRecord = records.find((item) => item.id === selectedContextId);
  const invoiceContext =
    selectedRecord?.status === "done" &&
    !(editing && selectedRecord.id === activeId)
      ? selectedRecord
      : undefined;
  const threadKey = invoiceContext
    ? `${invoiceContext.id}:${invoiceContext.revision}`
    : "general";
  const messages = threads[threadKey] || [];
  const processing = records.some((item) => item.status !== "done");

  let unavailable = "";
  if (!mode) unavailable = "Connecting to the local invoice service…";
  else if (mode === "demo")
    unavailable =
      "Invoice chat needs the local Qwen model and is unavailable in demo mode.";
  else if (mode !== "max")
    unavailable = "The local invoice assistant is unavailable.";

  let contextNote = "";
  if (editing && selectedRecord?.id === activeId)
    contextNote =
      "Save or cancel your edits to include this invoice. You can still ask a general question.";
  else if (selectedRecord && selectedRecord.status !== "done")
    contextNote =
      "This invoice is still being read. Your question will use general mode for now.";
  else if (!invoiceContext)
    contextNote =
      "Ask anything. For details about a specific invoice, select it above.";
  else if (processing)
    contextNote =
      "Other invoices can keep processing while you ask about this one.";

  useEffect(() => {
    setContextChoice("auto");
  }, [activeId]);

  useEffect(() => {
    if (
      contextChoice !== "auto" &&
      contextChoice &&
      !completed.some((item) => item.id === contextChoice)
    ) {
      setContextChoice("");
    }
  }, [contextChoice, completed]);

  useEffect(() => {
    if (open && !unavailable) inputRef.current?.focus();
  }, [open, unavailable, threadKey]);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => {
      const log = messagesRef.current;
      if (log) log.scrollTop = log.scrollHeight;
    });
  }, [messages.length, open, sending]);

  useEffect(() => {
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && open) {
        setOpen(false);
        requestAnimationFrame(() => launcherRef.current?.focus());
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  useEffect(
    () => () => {
      requestRef.current?.abort();
    },
    [],
  );

  const close = () => {
    setOpen(false);
    requestAnimationFrame(() => launcherRef.current?.focus());
  };

  const clear = () => {
    if (!threadKey) return;
    setThreads((current) => {
      const next = { ...current };
      delete next[threadKey];
      return next;
    });
    setError("");
    inputRef.current?.focus();
  };

  const ask = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = question.trim();
    if (unavailable || sending || !text) return;

    const prior = historyFor(messages);
    const userId = crypto.randomUUID();
    setThreads((current) => ({
      ...current,
      [threadKey]: [
        ...(current[threadKey] || []),
        { id: userId, role: "user", content: text, pending: true },
      ],
    }));
    setQuestion("");
    setError("");
    setSending(true);
    const controller = new AbortController();
    requestRef.current = controller;

    try {
      const answer = await api<ChatAnswer>("/chat", {
        ...json("POST", {
          question: text,
          history: prior,
          ...(invoiceContext
            ? {
                invoice_id: invoiceContext.id,
                revision: invoiceContext.revision,
              }
            : {}),
        }),
        signal: controller.signal,
      });
      if (
        invoiceContext &&
        (answer.invoice_id !== invoiceContext.id ||
          answer.revision !== invoiceContext.revision)
      ) {
        throw new Error("The invoice changed before the answer was ready.");
      }
      setThreads((current) => ({
        ...current,
        [threadKey]: [
          ...(current[threadKey] || []).map((message) =>
            message.id === userId ? { ...message, pending: false } : message,
          ),
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: answer.answer,
            evidence: answer.evidence,
            showInvoiceHint: answer.invoice_id === null,
          },
        ],
      }));
    } catch (caught) {
      if (controller.signal.aborted) return;
      setThreads((current) => ({
        ...current,
        [threadKey]: (current[threadKey] || []).map((message) =>
          message.id === userId
            ? { ...message, pending: false, failed: true }
            : message,
        ),
      }));
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      if (!controller.signal.aborted) setSending(false);
      if (requestRef.current === controller) requestRef.current = null;
    }
  };

  const onComposerKeyDown = (
    event: ReactKeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask();
    }
  };

  return (
    <aside className={`invoice-chat ${open ? "is-open" : ""}`}>
      {open && (
        <section
          id="invoice-chat-panel"
          className="chat-panel"
          role="dialog"
          aria-modal="false"
          aria-labelledby="invoice-chat-title"
        >
          <header className="chat-header">
            <span className="chat-bot-mark" aria-hidden="true">
              <Bot size={19} />
            </span>
            <div>
              <h2 id="invoice-chat-title">Invoice assistant</h2>
              <p>
                <span
                  className={`chat-live-dot ${mode === "max" ? "" : "offline"}`}
                />{" "}
                Local AI ·{" "}
                {mode === "max"
                  ? "read-only"
                  : mode
                    ? "unavailable"
                    : "connecting"}
              </p>
            </div>
            {messages.length > 0 && (
              <button
                className="chat-icon-button"
                type="button"
                onClick={clear}
                aria-label="Clear this conversation"
                title="Clear this conversation"
                disabled={sending}
              >
                <RotateCcw size={16} />
              </button>
            )}
            <button
              className="chat-icon-button"
              type="button"
              onClick={close}
              aria-label="Close invoice assistant"
            >
              <X size={18} />
            </button>
          </header>

          <div className="chat-context">
            <label>
              Ask about <span>(optional)</span>
              <span className="chat-select-wrap">
                <select
                  value={selectedContextId}
                  onChange={(event) => {
                    setContextChoice(event.target.value);
                    setError("");
                  }}
                  disabled={sending}
                >
                  <option value="">No invoice · General conversation</option>
                  {selectedRecord &&
                    selectedRecord.status !== "done" &&
                    selectedContextId && (
                      <option value={selectedContextId} disabled>
                        {contextLabel(selectedRecord)} · Processing
                      </option>
                    )}
                  {completed.map((item) => (
                    <option value={item.id} key={item.id}>
                      {contextLabel(item)}
                    </option>
                  ))}
                </select>
                <ChevronDown size={15} aria-hidden="true" />
              </span>
            </label>
            {contextNote && <p className="chat-context-note">{contextNote}</p>}
          </div>

          <div
            className="chat-messages"
            role="log"
            aria-live="polite"
            ref={messagesRef}
          >
            {messages.length === 0 && (
              <div className="chat-welcome">
                <Sparkles size={20} aria-hidden="true" />
                <h3>
                  {invoiceContext ? "Ask about the details" : "How can I help?"}
                </h3>
                <p>
                  {invoiceContext
                    ? "I can explain extracted fields, findings, purchase-order differences and review status using this invoice."
                    : "Write any question below. Select an invoice above whenever you want answers grounded in its details."}
                </p>
                {!unavailable && invoiceContext && (
                  <div className="chat-suggestions">
                    {invoiceSuggestions.map((suggestion) => (
                      <button
                        type="button"
                        key={suggestion}
                        onClick={() => {
                          setQuestion(suggestion);
                          inputRef.current?.focus();
                        }}
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {messages.map((message) => (
              <article
                className={`chat-message ${message.role} ${message.failed ? "failed" : ""}`}
                key={message.id}
              >
                <span>{message.role === "user" ? "You" : "Local AI"}</span>
                <p>{message.content}</p>
                {message.pending && (
                  <small>
                    <LoaderCircle className="spin" size={13} /> Thinking…
                  </small>
                )}
                {message.failed && <small>Not sent to the conversation.</small>}
                {message.evidence && message.evidence.length > 0 && (
                  <details className="chat-evidence">
                    <summary>Evidence used ({message.evidence.length})</summary>
                    {message.evidence.map((item) => (
                      <div key={item.id}>
                        <strong>{item.label}</strong>
                        <span>{item.value}</span>
                      </div>
                    ))}
                  </details>
                )}
                {message.showInvoiceHint && (
                  <small className="chat-invoice-hint">
                    Want details about an invoice? Select it above in “Ask
                    about”.
                  </small>
                )}
              </article>
            ))}
          </div>

          {(unavailable || error) && (
            <p
              className={error ? "chat-error" : "chat-unavailable"}
              role={error ? "alert" : "status"}
            >
              {error || unavailable}
            </p>
          )}

          <form className="chat-composer" onSubmit={(event) => void ask(event)}>
            <label className="sr-only" htmlFor="invoice-chat-question">
              Ask the local AI a question
            </label>
            <textarea
              id="invoice-chat-question"
              ref={inputRef}
              rows={1}
              maxLength={1000}
              value={question}
              placeholder="Ask anything…"
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={onComposerKeyDown}
              disabled={Boolean(unavailable) || sending}
            />
            <button
              type="submit"
              aria-label="Send question"
              disabled={Boolean(unavailable) || sending || !question.trim()}
            >
              {sending ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <Send size={18} />
              )}
            </button>
          </form>
          <p className="chat-disclaimer">
            Select an invoice for grounded details. The assistant cannot
            approve, edit, email or pay.
          </p>
        </section>
      )}

      <button
        className="chat-launcher"
        type="button"
        ref={launcherRef}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="invoice-chat-panel"
        aria-label={open ? "Close local AI chat" : "Chat with local AI"}
      >
        <span className="chat-launcher-icon">
          <MessageCircle size={18} />
        </span>
        <span>{open ? "Local AI chat is open" : "Chat with local AI"}</span>
        <span className="chat-launcher-action">
          <Sparkles size={16} />
        </span>
      </button>
    </aside>
  );
}
