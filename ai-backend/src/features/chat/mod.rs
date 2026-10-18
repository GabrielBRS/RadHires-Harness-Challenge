use crate::{
    ApiError, AppState,
    features::{
        agents::{ChatTurn, EvidenceItem},
        inbox::Record,
    },
};
use axum::{
    Json,
    extract::{Path, State},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AskInvoice {
    pub revision: u64,
    pub question: String,
    #[serde(default)]
    pub history: Vec<ChatTurn>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AskChat {
    pub question: String,
    #[serde(default)]
    pub history: Vec<ChatTurn>,
    pub invoice_id: Option<String>,
    pub revision: Option<u64>,
}

#[derive(Serialize)]
pub struct InvoiceAnswer {
    pub invoice_id: String,
    pub revision: u64,
    pub answer: String,
    pub cannot_answer: bool,
    pub evidence: Vec<EvidenceItem>,
    pub provider: String,
    pub model: String,
}

#[derive(Serialize)]
pub struct ChatAnswer {
    pub invoice_id: Option<String>,
    pub revision: Option<u64>,
    pub answer: String,
    pub cannot_answer: bool,
    pub uses_invoice_context: bool,
    pub evidence: Vec<EvidenceItem>,
    pub provider: String,
    pub model: String,
}

fn current(record: &Record, revision: u64) -> Result<(), ApiError> {
    if record.revision != revision {
        Err(ApiError::conflict())
    } else if record.status != "done" {
        Err(ApiError::bad("Wait for processing to finish"))
    } else {
        Ok(())
    }
}

fn validate_conversation(question: &str, history: &[ChatTurn]) -> Result<(), ApiError> {
    if question.is_empty() || question.chars().count() > 1000 {
        return Err(ApiError::bad("Ask a question of 1–1000 characters"));
    }
    let history_chars = history
        .iter()
        .map(|turn| turn.content.chars().count())
        .sum::<usize>();
    if history.len() > 6
        || !history.len().is_multiple_of(2)
        || history_chars > 4000
        || history.iter().enumerate().any(|(index, turn)| {
            turn.role != if index % 2 == 0 { "user" } else { "assistant" }
                || turn.content.trim().is_empty()
                || turn.content.chars().count() > 4000
        })
    {
        return Err(ApiError::bad("Conversation history is invalid or too long"));
    }
    Ok(())
}

fn push_evidence(
    items: &mut Vec<EvidenceItem>,
    id: impl Into<String>,
    label: impl Into<String>,
    value: Option<String>,
) {
    if let Some(value) = value.filter(|value| !value.trim().is_empty()) {
        items.push(EvidenceItem {
            id: id.into(),
            label: label.into(),
            value,
        });
    }
}

fn evidence_for(record: &Record) -> Vec<EvidenceItem> {
    let mut items = Vec::new();
    push_evidence(
        &mut items,
        "document:filename",
        "Document filename",
        Some(record.filename.clone()),
    );
    let Some(result) = &record.result else {
        return items;
    };
    if let Some(invoice) = &result.extraction {
        push_evidence(
            &mut items,
            "invoice:supplier",
            "Supplier",
            invoice.supplier_name.clone(),
        );
        push_evidence(
            &mut items,
            "invoice:number",
            "Invoice number",
            invoice.invoice_number.clone(),
        );
        push_evidence(
            &mut items,
            "invoice:date",
            "Invoice date",
            invoice.invoice_date.clone(),
        );
        push_evidence(
            &mut items,
            "invoice:po_number",
            "Purchase order",
            invoice.po_number.clone(),
        );
        push_evidence(
            &mut items,
            "invoice:currency",
            "Currency",
            invoice.currency.clone(),
        );
        push_evidence(
            &mut items,
            "invoice:subtotal",
            "Subtotal",
            invoice.subtotal.map(|value| value.to_string()),
        );
        push_evidence(
            &mut items,
            "invoice:tax",
            "Tax",
            invoice.tax.map(|value| value.to_string()),
        );
        push_evidence(
            &mut items,
            "invoice:total",
            "Total",
            invoice.total.map(|value| value.to_string()),
        );
        for (index, line) in invoice.lines.iter().enumerate() {
            push_evidence(
                &mut items,
                format!("invoice:line:{index}"),
                format!("Invoice line {}", index + 1),
                Some(format!(
                    "{} | SKU {} | quantity {} | unit price {} | line total {}",
                    line.description,
                    line.sku.as_deref().unwrap_or("not provided"),
                    line.quantity,
                    line.unit_price,
                    line.line_total
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "not provided".into())
                )),
            );
        }
    }
    for (index, issue) in result.issues.iter().enumerate() {
        push_evidence(
            &mut items,
            format!("issue:{index}"),
            format!("Finding: {}", issue.title),
            Some(format!(
                "{} | severity {} | next step {}",
                issue.detail, issue.severity, issue.next_step
            )),
        );
    }
    if let Some(po) = &result.po {
        push_evidence(
            &mut items,
            "po:number",
            "Matched purchase order",
            Some(format!("{} | supplier {}", po.po_number, po.supplier_name)),
        );
        for (index, line) in po.lines.iter().enumerate() {
            push_evidence(
                &mut items,
                format!("po:line:{index}"),
                format!("Purchase-order line {}", index + 1),
                Some(format!(
                    "{} | SKU {} | received {} | unit price {}",
                    line.description, line.sku, line.qty_received, line.unit_price
                )),
            );
        }
    }
    push_evidence(
        &mut items,
        "status:automated_recommendation",
        "Automated check recommendation",
        Some(result.decision.clone()),
    );
    push_evidence(
        &mut items,
        "status:human_decision",
        "Human decision",
        Some(
            record
                .human_decision
                .clone()
                .unwrap_or_else(|| "No human decision recorded".into()),
        ),
    );
    items
}

pub async fn ask(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
    Json(input): Json<AskInvoice>,
) -> Result<Json<InvoiceAnswer>, ApiError> {
    let question = input.question.trim();
    validate_conversation(question, &input.history)?;

    let record = state.store.get(&id)?.ok_or(ApiError::not_found())?;
    current(&record, input.revision)?;
    if state.mode != "max" {
        return Err(ApiError::unavailable(
            "Invoice chat uses the local Qwen model and is unavailable in demo mode.",
        ));
    }
    let evidence = evidence_for(&record);
    let _permit = state.acquire_chat_worker().await.map_err(|error| {
        tracing::error!("Could not queue invoice chat: {error}");
        ApiError::unavailable("The local invoice assistant is unavailable.")
    })?;
    let draft = state
        .agents
        .answer_invoice(&evidence, question, &input.history)
        .await
        .map_err(|error| {
            tracing::error!("Invoice chat failed: {error}");
            ApiError::unavailable(
                "The local invoice assistant could not answer. Try a shorter question or review the invoice manually.",
            )
        })?;

    let latest = state.store.get(&id)?.ok_or(ApiError::not_found())?;
    current(&latest, input.revision)?;
    let by_id = evidence
        .iter()
        .map(|item| (item.id.as_str(), item))
        .collect::<HashMap<_, _>>();
    let mut used = HashSet::new();
    let mut cited = Vec::new();
    for evidence_id in draft.evidence_ids {
        if !used.insert(evidence_id.clone()) {
            continue;
        }
        let item = by_id.get(evidence_id.as_str()).ok_or_else(|| {
            ApiError::unavailable("The local invoice assistant returned unsupported evidence.")
        })?;
        cited.push((*item).clone());
    }

    Ok(Json(InvoiceAnswer {
        invoice_id: id,
        revision: input.revision,
        answer: draft.answer,
        cannot_answer: draft.cannot_answer,
        evidence: cited,
        provider: "max".into(),
        model: state.agents.model.clone(),
    }))
}

pub async fn ask_general(
    State(state): State<Arc<AppState>>,
    Json(input): Json<AskChat>,
) -> Result<Json<ChatAnswer>, ApiError> {
    let question = input.question.trim();
    validate_conversation(question, &input.history)?;
    if state.mode != "max" {
        return Err(ApiError::unavailable(
            "Chat uses the local Qwen model and is unavailable in demo mode.",
        ));
    }

    let context = match (&input.invoice_id, input.revision) {
        (None, None) => None,
        (Some(id), Some(revision)) if !id.trim().is_empty() => {
            let record = state.store.get(id)?.ok_or(ApiError::not_found())?;
            current(&record, revision)?;
            Some((id.clone(), revision, evidence_for(&record)))
        }
        _ => {
            return Err(ApiError::bad(
                "Send invoice_id and revision together, or omit both",
            ));
        }
    };

    let _permit = state.acquire_chat_worker().await.map_err(|error| {
        tracing::error!("Could not queue chat: {error}");
        ApiError::unavailable("The local assistant is unavailable.")
    })?;
    let draft = state
        .agents
        .answer_chat(
            context.as_ref().map(|(_, _, evidence)| evidence.as_slice()),
            question,
            &input.history,
        )
        .await
        .map_err(|error| {
            tracing::error!("Chat failed: {error}");
            ApiError::unavailable("The local assistant could not answer. Try a shorter question.")
        })?;

    if let Some((id, revision, _)) = &context {
        let latest = state.store.get(id)?.ok_or(ApiError::not_found())?;
        current(&latest, *revision)?;
    }
    let by_id = context
        .as_ref()
        .into_iter()
        .flat_map(|(_, _, evidence)| evidence)
        .map(|item| (item.id.as_str(), item))
        .collect::<HashMap<_, _>>();
    let mut used = HashSet::new();
    let mut cited = Vec::new();
    for evidence_id in draft.evidence_ids {
        if !used.insert(evidence_id.clone()) {
            continue;
        }
        let item = by_id.get(evidence_id.as_str()).ok_or_else(|| {
            ApiError::unavailable("The local assistant returned unsupported invoice evidence.")
        })?;
        cited.push((*item).clone());
    }
    let (invoice_id, revision) = context
        .map(|(id, revision, _)| (Some(id), Some(revision)))
        .unwrap_or((None, None));

    Ok(Json(ChatAnswer {
        invoice_id,
        revision,
        answer: draft.answer,
        cannot_answer: draft.cannot_answer,
        uses_invoice_context: draft.uses_invoice_context,
        evidence: cited,
        provider: "max".into(),
        model: state.agents.model.clone(),
    }))
}
