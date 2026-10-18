use crate::{
    ApiError, AppState,
    features::{
        checking::{self, models::*},
        inbox::Record,
    },
};
use axum::{
    Json,
    extract::{Path, State},
};
use serde::Deserialize;
use std::sync::Arc;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Edit {
    pub revision: u64,
    pub extraction: Invoice,
    pub note: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Decision {
    pub revision: u64,
    pub decision: String,
    pub note: String,
}
fn note(value: &str) -> Result<(), ApiError> {
    if value.trim().len() < 5 || value.len() > 2000 {
        Err(ApiError::bad("Please provide a note of 5–2000 characters"))
    } else {
        Ok(())
    }
}
fn editable(record: &Record, revision: u64) -> Result<(), ApiError> {
    if record.revision != revision {
        Err(ApiError::conflict())
    } else if record.status != "done" {
        Err(ApiError::bad("Wait for processing to finish"))
    } else {
        Ok(())
    }
}
pub async fn edit(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
    Json(input): Json<Edit>,
) -> Result<Json<Record>, ApiError> {
    note(&input.note)?;
    input
        .extraction
        .validate()
        .map_err(|e| ApiError::bad(&e.to_string()))?;
    let _guard = state.mutations.lock().await;
    let mut record = state.store.get(&id)?.ok_or(ApiError::not_found())?;
    editable(&record, input.revision)?;
    let bytes = state.store.pdf(&id)?.ok_or(ApiError::not_found())?;
    let source = tokio::task::spawn_blocking(move || checking::pdf::text(&bytes))
        .await
        .map_err(|e| ApiError::bad(&e.to_string()))?;
    let queue = state
        .store
        .list()?
        .into_iter()
        .filter(|r| r.id != id)
        .filter_map(|r| r.result)
        .collect::<Vec<_>>();
    let mut result = checking::check(
        input.extraction,
        source.as_deref().unwrap_or(""),
        &state.reference,
        &queue,
        "manual",
    );
    if let Err(e) = source {
        result.issues.push(issue(
            "UNREADABLE",
            "review",
            "Original document needs manual review",
            &e.to_string(),
        ));
        result.decide();
    }
    // Corrections invalidate previous approval and AI verification; retain traces as history, not new evidence.
    if let Some(old) = &record.result {
        result.agent_runs = old.agent_runs.clone();
    }
    record.result = Some(result);
    record.human_decision = None;
    record.revision += 1;
    record.log("edited", input.note.trim());
    if !state.store.update(&record, input.revision)? {
        return Err(ApiError::conflict());
    }
    Ok(Json(record))
}
pub async fn decide(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
    Json(input): Json<Decision>,
) -> Result<Json<Record>, ApiError> {
    let _guard = state.mutations.lock().await;
    let mut record = state.store.get(&id)?.ok_or(ApiError::not_found())?;
    editable(&record, input.revision)?;
    if !["approved", "held", "queried", "undo"].contains(&input.decision.as_str()) {
        return Err(ApiError::bad("Unknown review decision"));
    }
    if input.note.len() > 2000 {
        return Err(ApiError::bad("Note is too long"));
    }
    if input.decision == "approved" {
        let result = record
            .result
            .as_ref()
            .ok_or(ApiError::bad("No invoice information to approve"))?;
        let ex = result
            .extraction
            .as_ref()
            .ok_or(ApiError::bad("Enter invoice information before approval"))?;
        if ex
            .invoice_number
            .as_deref()
            .is_none_or(|s| s.trim().is_empty())
            || ex
                .supplier_name
                .as_deref()
                .is_none_or(|s| s.trim().is_empty())
            || ex.currency.as_deref() != Some("USD")
            || ex.total.is_none_or(|n| n <= 0.)
            || ex.lines.is_empty()
        {
            return Err(ApiError::bad(
                "Approval requires a supplier, invoice number, positive USD total and line items",
            ));
        }
        // Recompute duplicate risk at approval time so edits to another invoice cannot bypass it.
        let queue = state
            .store
            .list()?
            .into_iter()
            .filter(|r| r.id != id)
            .filter_map(|r| r.result)
            .collect::<Vec<_>>();
        let bytes = state.store.pdf(&id)?.ok_or(ApiError::not_found())?;
        let source = tokio::task::spawn_blocking(move || checking::pdf::text(&bytes))
            .await
            .map_err(|e| ApiError::bad(&e.to_string()))?;
        let mut fresh = checking::check(
            ex.clone(),
            source.as_deref().unwrap_or(""),
            &state.reference,
            &queue,
            &result.extractor,
        );
        fresh.issues.extend(
            result
                .issues
                .iter()
                .filter(|i| i.code.starts_with("VERIFIER_") || i.code == "UNREADABLE")
                .cloned(),
        );
        fresh.agent_runs = result.agent_runs.clone();
        fresh.decide();
        if fresh.decision != "approve" {
            note(&input.note)?;
        }
        record.result = Some(fresh);
    }
    record.human_decision = if input.decision == "undo" {
        None
    } else {
        Some(input.decision.clone())
    };
    record.revision += 1;
    record.log(&input.decision, input.note.trim());
    if !state.store.update(&record, input.revision)? {
        return Err(ApiError::conflict());
    }
    Ok(Json(record))
}
pub async fn draft(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let record = state.store.get(&id)?.ok_or(ApiError::not_found())?;
    let result = record
        .result
        .as_ref()
        .ok_or(ApiError::bad("Wait for processing to finish"))?;
    let number = result
        .extraction
        .as_ref()
        .and_then(|e| e.invoice_number.as_deref())
        .unwrap_or(&record.filename);
    let concerns = result
        .issues
        .iter()
        .filter(|i| i.severity != "info" && !i.code.starts_with("VERIFIER_"))
        .map(|i| format!("- {}: {}", i.title, i.detail))
        .collect::<Vec<_>>()
        .join("\n");
    Ok(Json(
        serde_json::json!({"subject":format!("Question about invoice {number}"),"body":format!("Hello,\n\nBefore we process invoice {number}, could you help us confirm the following?\n\n{concerns}\n\nPlease send clarification or a corrected invoice.\n\nThank you,\nAccounts payable"),"sent":false}),
    ))
}
