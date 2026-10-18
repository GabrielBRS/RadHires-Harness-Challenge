pub mod store;
use crate::{
    ApiError, AppState,
    features::checking::{self, models::CheckResult},
};
use axum::{
    Json,
    extract::{Multipart, Path, State},
    http::{StatusCode, header},
    response::IntoResponse,
};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Audit {
    pub at: String,
    pub action: String,
    pub note: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Record {
    pub id: String,
    pub filename: String,
    pub status: String,
    pub revision: u64,
    pub created_at: String,
    pub result: Option<CheckResult>,
    pub human_decision: Option<String>,
    pub audit: Vec<Audit>,
}
impl Record {
    pub fn new(filename: String) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            filename,
            status: "waiting".into(),
            revision: 0,
            created_at: Utc::now().to_rfc3339(),
            result: None,
            human_decision: None,
            audit: vec![],
        }
    }
    pub fn log(&mut self, action: &str, note: &str) {
        self.audit.push(Audit {
            at: Utc::now().to_rfc3339(),
            action: action.into(),
            note: note.into(),
        });
    }
}
pub async fn list(State(state): State<Arc<AppState>>) -> Result<Json<Vec<Record>>, ApiError> {
    Ok(Json(state.store.list()?))
}
pub async fn detail(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<Json<Record>, ApiError> {
    Ok(Json(state.store.get(&id)?.ok_or(ApiError::not_found())?))
}
pub async fn clear(State(state): State<Arc<AppState>>) -> Result<StatusCode, ApiError> {
    let _guard = state.mutations.lock().await;
    state.store.clear()?;
    Ok(StatusCode::NO_CONTENT)
}
pub async fn delete(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    let _guard = state.mutations.lock().await;
    if !state.store.delete(&id)? {
        return Err(ApiError::not_found());
    }
    Ok(StatusCode::NO_CONTENT)
}
pub async fn pdf(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    let bytes = state.store.pdf(&id)?.ok_or(ApiError::not_found())?;
    Ok((
        [
            (header::CONTENT_TYPE, "application/pdf"),
            (header::CACHE_CONTROL, "no-store"),
            (header::CONTENT_DISPOSITION, "inline; filename=invoice.pdf"),
        ],
        bytes,
    ))
}
pub async fn upload(
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
) -> Result<(StatusCode, Json<Vec<Record>>), ApiError> {
    let mut pending = vec![];
    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|e| ApiError::bad(&e.to_string()))?
    {
        let filename = field
            .file_name()
            .unwrap_or("invoice.pdf")
            .replace(['/', '\\'], "_");
        let bytes = field
            .bytes()
            .await
            .map_err(|e| ApiError::bad(&e.to_string()))?;
        if !filename.to_lowercase().ends_with(".pdf")
            || !bytes.starts_with(b"%PDF-")
            || bytes.len() > 20 * 1024 * 1024
        {
            return Err(ApiError::bad("Choose PDF files up to 20 MB each"));
        }
        if pending.len() >= 20 {
            return Err(ApiError::bad("Upload at most 20 files at a time"));
        }
        pending.push((filename, bytes));
    }
    if pending.is_empty() {
        return Err(ApiError::bad("Choose at least one PDF"));
    }
    let mut records = vec![];
    let _guard = state.mutations.lock().await;
    // Validate capacity for the entire batch before inserting anything.
    let (existing_count, existing_bytes) = state.store.usage()?;
    if existing_count + pending.len() > 100
        || existing_bytes + pending.iter().map(|p| p.1.len()).sum::<usize>() > 200 * 1024 * 1024
    {
        return Err(ApiError::bad(
            "Session is full. Export and clear the queue.",
        ));
    }
    for (name, bytes) in pending {
        let record = Record::new(name);
        state.store.insert(&record, &bytes)?;
        records.push(record);
    }
    for r in &records {
        start(state.clone(), r.id.clone());
    }
    Ok((StatusCode::ACCEPTED, Json(records)))
}
pub async fn samples(
    State(state): State<Arc<AppState>>,
) -> Result<(StatusCode, Json<Vec<Record>>), ApiError> {
    let _guard = state.mutations.lock().await;
    let mut paths = std::fs::read_dir(state.root.join("data/samples"))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "pdf"))
        .collect::<Vec<_>>();
    paths.sort();
    let (existing_count, existing_bytes) = state.store.usage()?;
    let samples = paths
        .into_iter()
        .map(|path| {
            let bytes = std::fs::read(&path)?;
            Ok((path, bytes))
        })
        .collect::<Result<Vec<_>, std::io::Error>>()?;
    if existing_count + samples.len() > 100
        || existing_bytes + samples.iter().map(|p| p.1.len()).sum::<usize>() > 200 * 1024 * 1024
    {
        return Err(ApiError::bad(
            "Session is full. Export and clear the queue.",
        ));
    }
    let mut records = vec![];
    for (path, bytes) in samples {
        let record = Record::new(path.file_name().unwrap().to_string_lossy().into());
        state.store.insert(&record, &bytes)?;
        records.push(record);
    }
    for r in &records {
        start(state.clone(), r.id.clone());
    }
    Ok((StatusCode::ACCEPTED, Json(records)))
}
fn start(state: Arc<AppState>, id: String) {
    tokio::spawn(async move {
        if let Err(e) = process(state.clone(), &id).await {
            tracing::error!("Invoice processing failed: {e}");
            if let Ok(Some(mut record)) = state.store.get(&id) {
                let expected = record.revision;
                record.status = "done".into();
                record.revision += 1;
                record.result = Some(CheckResult::failed(
                    "PROCESSING_FAILED",
                    "Could not finish processing. Please retry or review manually.",
                    &state.mode,
                ));
                let _ = state.store.update(&record, expected);
            }
        }
    });
}
async fn process(state: Arc<AppState>, id: &str) -> anyhow::Result<()> {
    // Invoice jobs poll instead of joining the semaphore's FIFO queue so an interactive
    // chat can run immediately after the current inference finishes.
    let _permit = state.acquire_invoice_worker().await?;
    let Some(mut record) = state.store.get(id)? else {
        return Ok(());
    };
    let expected = record.revision;
    record.status = "reading".into();
    record.revision += 1;
    if !state.store.update(&record, expected)? {
        return Ok(());
    }
    let Some(bytes) = state.store.pdf(id)? else {
        return Ok(());
    };
    let text = tokio::task::spawn_blocking(move || checking::pdf::text(&bytes)).await?;
    let mut result = match text.as_ref() {
        Ok(text) => {
            if state.mode == "demo" {
                match checking::baseline::extract(text) {
                    Ok(ex) => checking::check(ex, text, &state.reference, &[], "demo"),
                    Err(e) => CheckResult::failed("READER_FAILED", &e.to_string(), "demo"),
                }
            } else {
                state.agents.check(text, &state.reference, &[]).await
            }
        }
        Err(e) => CheckResult::failed("UNREADABLE", &e.to_string(), &state.mode),
    };
    // Serialize final checks with manual edits/decisions. A cleared or revised item cannot reappear.
    let _guard = state.mutations.lock().await;
    if let (Some(ex), Ok(text)) = (&result.extraction, &text) {
        let queued = state
            .store
            .list()?
            .into_iter()
            .filter(|r| r.id != id)
            .filter_map(|r| r.result)
            .collect::<Vec<_>>();
        let mut updated = checking::check(ex.clone(), text, &state.reference, &queued, &state.mode);
        updated.issues.extend(
            result
                .issues
                .into_iter()
                .filter(|i| i.code.starts_with("VERIFIER_")),
        );
        updated.agent_runs = result.agent_runs;
        updated.decide();
        result = updated;
    }
    let expected = record.revision;
    record.revision += 1;
    record.status = "done".into();
    record.result = Some(result);
    record.log("checked", &format!("Reader: {}", state.mode));
    state.store.update(&record, expected)?;
    Ok(())
}
