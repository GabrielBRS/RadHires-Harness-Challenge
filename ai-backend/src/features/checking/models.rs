use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Invoice {
    pub supplier_name: Option<String>,
    pub invoice_number: Option<String>,
    pub invoice_date: Option<String>,
    pub po_number: Option<String>,
    pub currency: Option<String>,
    pub lines: Vec<LineItem>,
    pub subtotal: Option<f64>,
    pub tax: Option<f64>,
    pub total: Option<f64>,
}
#[derive(Clone, Debug, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct LineItem {
    pub description: String,
    pub sku: Option<String>,
    pub quantity: f64,
    pub unit_price: f64,
    pub line_total: Option<f64>,
}
impl Invoice {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.lines.len() <= 100,
            "At most 100 invoice lines are supported"
        );
        for n in [self.subtotal, self.tax, self.total]
            .into_iter()
            .flatten()
            .chain(self.lines.iter().flat_map(|l| {
                [Some(l.quantity), Some(l.unit_price), l.line_total]
                    .into_iter()
                    .flatten()
            }))
        {
            anyhow::ensure!(
                n.is_finite() && n.abs() <= 1e9,
                "Amounts must be finite and below one billion"
            );
        }
        anyhow::ensure!(
            serde_json::to_vec(self)?.len() < 32_000,
            "Invoice fields are too long"
        );
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PoLine {
    pub line: usize,
    pub sku: String,
    pub description: String,
    pub qty_received: f64,
    pub unit_price: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PurchaseOrder {
    pub po_number: String,
    pub supplier_id: String,
    pub supplier_name: String,
    pub lines: Vec<PoLine>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PaidLine {
    pub sku: String,
    pub quantity: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PaidInvoice {
    pub supplier_id: String,
    pub invoice_number: String,
    pub invoice_date: String,
    pub po_number: String,
    pub total: f64,
    pub lines: Vec<PaidLine>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Issue {
    pub code: String,
    pub severity: String,
    pub title: String,
    pub detail: String,
    pub next_step: String,
    pub line_index: Option<usize>,
    pub amount_at_stake: Option<f64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AgentTrace {
    pub agent: String,
    pub provider: String,
    pub model: String,
    pub status: String,
    pub seconds: f64,
    pub error: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CheckResult {
    pub extraction: Option<Invoice>,
    pub issues: Vec<Issue>,
    pub decision: String,
    pub supplier_id: Option<String>,
    pub po: Option<PurchaseOrder>,
    pub agent_runs: Vec<AgentTrace>,
    pub extractor: String,
}
impl CheckResult {
    pub fn decide(&mut self) {
        self.decision = if self.issues.iter().any(|i| i.severity == "block") {
            "hold"
        } else if self.issues.iter().any(|i| i.severity == "review") || self.extraction.is_none() {
            "review"
        } else {
            "approve"
        }
        .into();
    }
    pub fn failed(code: &str, detail: &str, extractor: &str) -> Self {
        Self {
            extraction: None,
            issues: vec![issue(
                code,
                "review",
                "This document needs attention",
                detail,
            )],
            decision: "review".into(),
            supplier_id: None,
            po: None,
            agent_runs: vec![],
            extractor: extractor.into(),
        }
    }
}
pub fn issue(code: &str, severity: &str, title: &str, detail: &str) -> Issue {
    Issue {
        code: code.into(),
        severity: severity.into(),
        title: title.into(),
        detail: detail.into(),
        next_step:
            "Compare with the original document and resolve with purchasing or the supplier.".into(),
        line_index: None,
        amount_at_stake: None,
    }
}
