export type Line = {
  description: string;
  sku: string | null;
  quantity: number;
  unit_price: number;
  line_total: number | null;
};
export type Invoice = {
  supplier_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  po_number: string | null;
  currency: string | null;
  subtotal: number | null;
  tax: number | null;
  total: number | null;
  lines: Line[];
};
export type Issue = {
  code: string;
  severity: string;
  title: string;
  detail: string;
  next_step: string;
  line_index: number | null;
  amount_at_stake: number | null;
};
export type Result = {
  extraction: Invoice | null;
  issues: Issue[];
  decision: string;
  extractor: string;
  agent_runs: {
    agent: string;
    status: string;
    model: string;
    seconds: number;
    error: string | null;
  }[];
  po: {
    po_number: string;
    supplier_name: string;
    lines: {
      line: number;
      sku: string;
      description: string;
      qty_received: number;
      unit_price: number;
    }[];
  } | null;
};
export type Record = {
  id: string;
  filename: string;
  status: string;
  revision: number;
  created_at: string;
  result: Result | null;
  human_decision: string | null;
  audit: { at: string; action: string; note: string }[];
};
export type ChatTurn = {
  role: "user" | "assistant";
  content: string;
};
export type ChatEvidence = {
  id: string;
  label: string;
  value: string;
};
export type ChatAnswer = {
  invoice_id: string | null;
  revision: number | null;
  answer: string;
  cannot_answer: boolean;
  evidence: ChatEvidence[];
  provider: string;
  model: string;
  uses_invoice_context: boolean;
};
export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${url}`, init);
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      message = JSON.parse(text).error || text;
    } catch {
      /* Plain HTTP error. */
    }
    throw new Error(message || `Request failed (${response.status})`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const json = (method: string, value: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(value),
});
export const money = (n: number | null | undefined) =>
  n == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(n);
export const label = (r: Record) =>
  r.status !== "done"
    ? "Reading"
    : r.human_decision === "approved"
      ? "Approved"
      : r.human_decision === "held"
        ? "On hold"
        : r.human_decision === "queried"
          ? "Supplier queried"
          : r.result?.decision === "approve"
            ? "Ready to review"
            : r.result?.decision === "hold"
              ? "Hold suggested"
              : "Needs review";
export const tone = (r: Record) =>
  r.human_decision === "approved"
    ? "green"
    : r.status !== "done"
      ? "neutral"
      : r.result?.decision === "hold"
        ? "red"
        : r.result?.decision === "approve"
          ? "green"
          : "amber";
