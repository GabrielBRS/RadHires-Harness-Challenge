pub mod features;
use axum::{
    Json, Router,
    extract::DefaultBodyLimit,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use features::{agents::Agents, checking::Reference, inbox::store::Store};
use std::{
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::sync::{Mutex, Semaphore, SemaphorePermit, TryAcquireError};
pub struct AppState {
    pub store: Store,
    pub reference: Reference,
    pub agents: Agents,
    pub mode: String,
    pub root: PathBuf,
    pub worker: Semaphore,
    pub chat_waiters: AtomicUsize,
    pub mutations: Mutex<()>,
}
struct ChatWaiter<'a>(&'a AtomicUsize);
impl Drop for ChatWaiter<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}
impl AppState {
    pub fn new(root: PathBuf, mode: String, agents: Agents) -> anyhow::Result<Arc<Self>> {
        Ok(Arc::new(Self {
            store: Store::new()?,
            reference: Reference::default(),
            agents,
            mode,
            root,
            worker: Semaphore::new(1),
            chat_waiters: AtomicUsize::new(0),
            mutations: Mutex::new(()),
        }))
    }
    pub async fn acquire_chat_worker(&self) -> anyhow::Result<SemaphorePermit<'_>> {
        self.chat_waiters.fetch_add(1, Ordering::SeqCst);
        let waiter = ChatWaiter(&self.chat_waiters);
        let permit = self.worker.acquire().await?;
        drop(waiter);
        Ok(permit)
    }
    pub async fn acquire_invoice_worker(&self) -> anyhow::Result<SemaphorePermit<'_>> {
        loop {
            if self.chat_waiters.load(Ordering::SeqCst) == 0 {
                match self.worker.try_acquire() {
                    Ok(permit) if self.chat_waiters.load(Ordering::SeqCst) == 0 => {
                        return Ok(permit);
                    }
                    Ok(permit) => drop(permit),
                    Err(TryAcquireError::NoPermits) => {}
                    Err(TryAcquireError::Closed) => {
                        anyhow::bail!("Inference worker is closed")
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }
}
#[derive(Debug)]
pub struct ApiError(StatusCode, String);
impl ApiError {
    pub fn bad(s: &str) -> Self {
        Self(StatusCode::BAD_REQUEST, s.into())
    }
    pub fn not_found() -> Self {
        Self(
            StatusCode::NOT_FOUND,
            "Invoice no longer exists in this session".into(),
        )
    }
    pub fn conflict() -> Self {
        Self(
            StatusCode::CONFLICT,
            "This invoice changed. Reload before saving.".into(),
        )
    }
    pub fn unavailable(s: &str) -> Self {
        Self(StatusCode::SERVICE_UNAVAILABLE, s.into())
    }
    pub fn busy(s: &str) -> Self {
        Self(StatusCode::TOO_MANY_REQUESTS, s.into())
    }
}
impl<E: Into<anyhow::Error>> From<E> for ApiError {
    fn from(err: E) -> Self {
        tracing::error!("{}", err.into());
        Self(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not complete the operation".into(),
        )
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(serde_json::json!({"error":self.1}))).into_response()
    }
}
pub fn router(state: Arc<AppState>) -> Router {
    use features::{agents, chat, exporting, inbox, review};
    let mode = state.mode.clone();
    Router::new()
        .route("/api/health",get(move||async move{Json(serde_json::json!({"status":"ok","mode":mode,"storage":"sqlite-memory","session":"ephemeral"}))}))
        .route("/api/invoices",get(inbox::list).post(inbox::upload).delete(inbox::clear))
        .route("/api/samples",post(inbox::samples))
        .route("/api/invoices/{id}",get(inbox::detail).patch(review::edit).delete(inbox::delete))
        .route("/api/invoices/{id}/pdf",get(inbox::pdf))
        .route("/api/chat",post(chat::ask_general))
        .route("/api/invoices/{id}/chat",post(chat::ask))
        .route("/api/invoices/{id}/decision",post(review::decide))
        .route("/api/invoices/{id}/draft",get(review::draft))
        .route("/api/export.csv",get(exporting::csv))
        .route("/api/agents",get(||async{Json(agents::catalog())}))
        .layer(DefaultBodyLimit::max(40*1024*1024))
        .layer(axum::middleware::from_fn(|request:axum::extract::Request,next:axum::middleware::Next|async move {
            // Browser callers must use the local UI proxy. Reject cross-site writes to loopback.
            if request.method()!=axum::http::Method::GET && request.headers().get("sec-fetch-site").is_some_and(|v|v=="cross-site") {return (StatusCode::FORBIDDEN,"Cross-site requests are not allowed").into_response();}
            let mut response=next.run(request).await;response.headers_mut().insert("cache-control","no-store".parse().unwrap());response
        }))
        .with_state(state)
}
