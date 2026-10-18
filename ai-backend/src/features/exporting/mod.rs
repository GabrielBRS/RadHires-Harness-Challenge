use crate::{ApiError, AppState};
use axum::{extract::State, http::header, response::IntoResponse};
use std::sync::Arc;
pub fn safe(value: &str) -> String {
    if value.trim_start().starts_with(['=', '+', '-', '@']) || value.starts_with(['\t', '\r', '\n'])
    {
        format!("'{value}")
    } else {
        value.into()
    }
}
pub async fn csv(State(state): State<Arc<AppState>>) -> Result<impl IntoResponse, ApiError> {
    let mut writer = csv::Writer::from_writer(Vec::new());
    writer.write_record([
        "Supplier",
        "Invoice",
        "Date",
        "PO",
        "Currency",
        "Total",
        "Decision",
        "Review notes",
    ])?;
    for record in state
        .store
        .list()?
        .iter()
        .filter(|r| r.human_decision.as_deref() == Some("approved"))
    {
        if let Some(e) = record.result.as_ref().and_then(|r| r.extraction.as_ref()) {
            writer.write_record([
                safe(e.supplier_name.as_deref().unwrap_or("")),
                safe(e.invoice_number.as_deref().unwrap_or("")),
                safe(e.invoice_date.as_deref().unwrap_or("")),
                safe(e.po_number.as_deref().unwrap_or("")),
                safe(e.currency.as_deref().unwrap_or("")),
                format!("{:.2}", e.total.unwrap_or(0.)),
                "approved".into(),
                safe(
                    &record
                        .audit
                        .iter()
                        .map(|a| format!("{}: {}", a.action, a.note))
                        .collect::<Vec<_>>()
                        .join(" | "),
                ),
            ])?;
        }
    }
    let bytes = writer
        .into_inner()
        .map_err(|e| ApiError::bad(&e.to_string()))?;
    Ok((
        [
            (header::CONTENT_TYPE, "text/csv; charset=utf-8"),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=approved-invoices.csv",
            ),
            (header::CACHE_CONTROL, "no-store"),
        ],
        bytes,
    ))
}
