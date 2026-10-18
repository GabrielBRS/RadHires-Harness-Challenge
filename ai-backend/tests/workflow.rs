use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use invoice_check::{
    AppState,
    features::{
        agents::Agents,
        checking::{self, Reference, models::*},
        exporting::safe,
        inbox::{Record, store::Store},
    },
};
use serde_json::{Value, json};
use std::{path::PathBuf, sync::Arc};
use tower::ServiceExt;
fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .into()
}
fn fixtures() -> Vec<Value> {
    serde_json::from_str(include_str!("../../data/samples/ground_truth.json")).unwrap()
}
fn first() -> (Invoice, Vec<u8>, String) {
    let rows = fixtures();
    let ex = serde_json::from_value(rows[0]["extraction"].clone()).unwrap();
    let pdf = std::fs::read(root().join("data/samples/01_halvorsen_clean.pdf")).unwrap();
    let text = checking::pdf::text(&pdf).unwrap();
    (ex, pdf, text)
}
fn state() -> Arc<AppState> {
    AppState::new(
        root(),
        "demo".into(),
        Agents::new("http://127.0.0.1:1".into(), "test-qwen".into()).unwrap(),
    )
    .unwrap()
}
fn insert_checked_record(state: &Arc<AppState>) -> Record {
    let (ex, pdf, text) = first();
    let mut record = Record::new("sample.pdf".into());
    record.status = "done".into();
    record.result = Some(checking::check(ex, &text, &state.reference, &[], "oracle"));
    record.log("checked", "Reader: oracle");
    state.store.insert(&record, &pdf).unwrap();
    record
}
async fn request(
    app: &Router,
    method: &str,
    path: &str,
    value: Option<Value>,
) -> (StatusCode, Vec<u8>) {
    let body = value.as_ref().map(ToString::to_string).unwrap_or_default();
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(path)
                .header("content-type", "application/json")
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = response
        .into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec();
    (status, bytes)
}
#[test]
fn oracle_seventeen_documents_match_expected_decisions_and_findings() {
    let reference = Reference::default();
    for row in fixtures() {
        let name = row["file"].as_str().unwrap();
        let bytes = std::fs::read(root().join("data/samples").join(name)).unwrap();
        let result = match checking::pdf::text(&bytes) {
            Ok(text) => checking::check(
                serde_json::from_value(row["extraction"].clone()).unwrap(),
                &text,
                &reference,
                &[],
                "oracle",
            ),
            Err(e) => CheckResult::failed("UNREADABLE", &e.to_string(), "oracle"),
        };
        assert_eq!(
            result.decision, row["expected_decision"],
            "{name}: {:?}",
            result.issues
        );
        for expected in row["expected_issues"].as_array().unwrap() {
            assert!(
                result
                    .issues
                    .iter()
                    .any(|i| i.code == expected.as_str().unwrap()),
                "{name}: missing {expected}"
            );
        }
    }
}
#[test]
fn forty_nine_corrupted_extractions_fail_closed() {
    let reference = Reference::default();
    let mut count = 0;
    for row in fixtures()
        .into_iter()
        .filter(|v| v["expected_decision"] == "approve")
    {
        let bytes = std::fs::read(
            root()
                .join("data/samples")
                .join(row["file"].as_str().unwrap()),
        )
        .unwrap();
        let text = checking::pdf::text(&bytes).unwrap();
        for fault in 0..7 {
            let mut ex: Invoice = serde_json::from_value(row["extraction"].clone()).unwrap();
            match fault {
                0 => ex.lines[0].unit_price -= 2.,
                1 => {
                    ex.lines[0].unit_price -= 2.;
                    ex.lines[0].line_total = Some(ex.lines[0].unit_price * ex.lines[0].quantity);
                    let subtotal = ex.lines.iter().map(|l| l.line_total.unwrap()).sum::<f64>();
                    ex.subtotal = Some(subtotal);
                    ex.tax = Some((subtotal * 7.).round() / 100.);
                    ex.total = Some(subtotal + ex.tax.unwrap());
                }
                2 => ex.lines[0].quantity += 1.,
                3 => {
                    if ex.lines.len() > 1 {
                        ex.lines.remove(0);
                    } else {
                        ex.lines[0].quantity -= 1.;
                    }
                }
                4 => ex.lines.push(LineItem {
                    description: "Handling fee".into(),
                    sku: None,
                    quantity: 1.,
                    unit_price: 12.,
                    line_total: Some(12.),
                }),
                5 => {
                    ex.po_number = Some(
                        if ex.po_number.as_deref() == Some("PO-45000113") {
                            "PO-45000110"
                        } else {
                            "PO-45000113"
                        }
                        .into(),
                    )
                }
                _ => ex.total = ex.total.map(|t| t + 100.),
            }
            let result = checking::check(ex, &text, &reference, &[], "fault");
            assert_ne!(
                result.decision, "approve",
                "Unsafe approval: {} mutation {fault}",
                row["file"]
            );
            count += 1;
        }
    }
    assert_eq!(count, 49);
}
#[test]
fn memory_store_disappears_on_restart_and_rejects_stale_jobs() {
    let store = Store::new().unwrap();
    let mut record = Record::new("a.pdf".into());
    store.insert(&record, b"pdf").unwrap();
    let revision = record.revision;
    record.revision += 1;
    store.clear().unwrap();
    assert!(!store.update(&record, revision).unwrap());
    assert!(store.list().unwrap().is_empty());
    store.insert(&record, b"pdf").unwrap();
    drop(store);
    assert!(Store::new().unwrap().list().unwrap().is_empty());
}
#[test]
fn malformed_and_scanned_pdfs_are_not_silently_read() {
    assert!(checking::pdf::text(b"%PDF-garbage").is_err());
    let bytes = std::fs::read(root().join("data/samples/17_scanned_fax.pdf")).unwrap();
    assert!(checking::pdf::text(&bytes).is_err());
}
#[test]
fn queue_duplicates_are_not_described_as_paid() {
    let (ex, _, text) = first();
    let a = checking::check(ex.clone(), &text, &Reference::default(), &[], "oracle");
    let b = checking::check(ex, &text, &Reference::default(), &[a], "oracle");
    let issue = b
        .issues
        .iter()
        .find(|i| i.code == "DUPLICATE_INVOICE")
        .unwrap();
    assert!(issue.detail.contains("session's queue"));
    assert!(!issue.detail.contains("already paid"));
    assert_eq!(b.decision, "hold");
}
#[test]
fn csv_formula_prefixes_are_neutralized() {
    for value in [
        "=cmd()", " +danger", "-danger", "@danger", "\tdanger", "\rdanger", "\ndanger",
    ] {
        assert!(safe(value).starts_with('\''));
    }
    assert_eq!(safe("ACME, Inc"), "ACME, Inc");
}
#[tokio::test]
async fn review_edit_approval_conflict_export_and_clear() {
    let state = state();
    let (mut ex, pdf, text) = first();
    let mut record = Record::new("sample.pdf".into());
    record.status = "done".into();
    record.result = Some(checking::check(
        ex.clone(),
        &text,
        &state.reference,
        &[],
        "oracle",
    ));
    state.store.insert(&record, &pdf).unwrap();
    let app = invoice_check::router(state.clone());
    let path = format!("/api/invoices/{}", record.id);
    assert_eq!(
        request(
            &app,
            "POST",
            &format!("{path}/decision"),
            Some(json!({"revision":0,"decision":"approved","note":""}))
        )
        .await
        .0,
        StatusCode::OK
    );
    let (status, body) = request(&app, "GET", "/api/export.csv", None).await;
    assert_eq!(status, StatusCode::OK);
    assert!(String::from_utf8(body).unwrap().contains("HF-10231"));
    ex.total = Some(1100.);
    assert_eq!(
        request(
            &app,
            "PATCH",
            &path,
            Some(json!({"revision":0,"extraction":ex,"note":"Corrected the printed total"}))
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    let (status, body) = request(
        &app,
        "PATCH",
        &path,
        Some(json!({"revision":1,"extraction":ex,"note":"Corrected the printed total"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let updated: Record = serde_json::from_slice(&body).unwrap();
    assert!(updated.human_decision.is_none());
    assert_ne!(updated.result.unwrap().decision, "approve");
    assert_eq!(updated.audit.len(), 2);
    assert_eq!(
        request(
            &app,
            "POST",
            &format!("{path}/decision"),
            Some(json!({"revision":2,"decision":"approved","note":""}))
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(request(&app,"POST",&format!("{path}/decision"),Some(json!({"revision":2,"decision":"approved","note":"Purchasing confirmed the override"}))).await.0,StatusCode::OK);
    assert_eq!(
        request(
            &app,
            "POST",
            &format!("{path}/decision"),
            Some(json!({"revision":3,"decision":"undo","note":""}))
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(
        request(&app, "GET", &format!("{path}/pdf"), None).await.1,
        pdf
    );
    let csv = request(&app, "GET", "/api/export.csv", None).await.1;
    assert!(!String::from_utf8(csv).unwrap().contains("HF-10231"));
    assert_eq!(
        request(&app, "DELETE", "/api/invoices", None).await.0,
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        request(&app, "GET", &path, None).await.0,
        StatusCode::NOT_FOUND
    );
}
#[tokio::test]
async fn sample_queue_and_manual_entry_work_without_ai() {
    let state = state();
    let app = invoice_check::router(state.clone());
    assert_eq!(
        request(&app, "POST", "/api/samples", None).await.0,
        StatusCode::ACCEPTED
    );
    for _ in 0..200 {
        if state
            .store
            .list()
            .unwrap()
            .iter()
            .all(|r| r.status == "done")
        {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
    let rows = state.store.list().unwrap();
    assert_eq!(rows.len(), 17);
    assert!(rows.iter().all(|r| r.status == "done"));
    assert!(
        rows.iter()
            .any(|r| r.result.as_ref().unwrap().extraction.is_some())
    );
    let scan = rows
        .iter()
        .find(|r| r.filename.contains("scanned"))
        .unwrap();
    assert!(scan.result.as_ref().unwrap().extraction.is_none());
    assert_eq!(
        request(
            &app,
            "POST",
            &format!("/api/invoices/{}/decision", scan.id),
            Some(
                json!({"revision":scan.revision,"decision":"approved","note":"I reviewed the scan"})
            )
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    let (ex, _, _) = first();
    let (status,body)=request(&app,"PATCH",&format!("/api/invoices/{}",scan.id),Some(json!({"revision":scan.revision,"extraction":ex,"note":"Manually transcribed for review"}))).await;
    assert_eq!(status, StatusCode::OK);
    let record: Record = serde_json::from_slice(&body).unwrap();
    assert_ne!(record.result.unwrap().decision, "approve");
}
#[tokio::test]
async fn upload_validates_and_processes_real_pdf() {
    let state = state();
    let app = invoice_check::router(state.clone());
    let (_, pdf, _) = first();
    let mut body=b"--invoice-boundary\r\nContent-Disposition: form-data; name=\"files\"; filename=\"sample.pdf\"\r\nContent-Type: application/pdf\r\n\r\n".to_vec();
    body.extend(pdf);
    body.extend(b"\r\n--invoice-boundary--\r\n");
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/invoices")
                .header(
                    "content-type",
                    "multipart/form-data; boundary=invoice-boundary",
                )
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    for _ in 0..100 {
        if state.store.list().unwrap()[0].status == "done" {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
    assert!(
        state.store.list().unwrap()[0]
            .result
            .as_ref()
            .unwrap()
            .extraction
            .is_some()
    );
    assert_eq!(
        request(&app, "POST", "/api/invoices", Some(json!({"bad":"format"})))
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
}
#[tokio::test]
async fn cross_site_mutation_is_rejected() {
    let app = invoice_check::router(state());
    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/api/invoices")
                .header("sec-fetch-site", "cross-site")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
#[tokio::test]
async fn chat_waiters_run_before_queued_invoice_jobs() {
    use std::sync::atomic::Ordering;
    use tokio::{sync::mpsc, time::Duration};

    let state = state();
    let blocker = state.worker.acquire().await.unwrap();
    let (send, mut receive) = mpsc::unbounded_channel();

    let invoice_state = state.clone();
    let invoice_send = send.clone();
    let invoice = tokio::spawn(async move {
        let _permit = invoice_state.acquire_invoice_worker().await.unwrap();
        invoice_send.send("invoice").unwrap();
    });
    tokio::time::sleep(Duration::from_millis(20)).await;

    let chat_state = state.clone();
    let chat = tokio::spawn(async move {
        let _permit = chat_state.acquire_chat_worker().await.unwrap();
        send.send("chat").unwrap();
    });
    while state.chat_waiters.load(Ordering::SeqCst) == 0 {
        tokio::task::yield_now().await;
    }
    drop(blocker);

    assert_eq!(
        tokio::time::timeout(Duration::from_secs(1), receive.recv())
            .await
            .unwrap(),
        Some("chat")
    );
    assert_eq!(
        tokio::time::timeout(Duration::from_secs(1), receive.recv())
            .await
            .unwrap(),
        Some("invoice")
    );
    chat.await.unwrap();
    invoice.await.unwrap();
}
#[tokio::test]
async fn general_chat_works_without_an_invoice_and_uses_no_invoice_evidence() {
    use axum::{Json, routing::post};

    let max = Router::new().route(
        "/v1/chat/completions",
        post(|Json(body): Json<Value>| async move {
            assert_eq!(body["model"], "test-qwen");
            assert_eq!(body["max_tokens"], 768);
            assert!(
                body["messages"][0]["content"]
                    .as_str()
                    .unwrap()
                    .starts_with("You are Invoice Check's local, read-only assistant")
            );
            assert!(
                body["messages"][0]["content"]
                    .as_str()
                    .unwrap()
                    .contains("answer the question first")
            );
            let payload: Value =
                serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
            assert_eq!(payload["question"], "O que é uma nota fiscal?");
            assert!(payload["invoice_context"].is_null());
            assert_eq!(
                payload["conversation_history"][1]["content"]
                    .as_str()
                    .unwrap()
                    .chars()
                    .count(),
                1301
            );
            let content = json!({
                "answer": "Uma nota fiscal documenta uma operação comercial.",
                "cannot_answer": false,
                "uses_invoice_context": false,
                "evidence_ids": []
            })
            .to_string();
            Json(json!({
                "choices": [{"finish_reason": "stop", "message": {"content": content}}]
            }))
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, max).await.unwrap();
    });
    let state = AppState::new(
        root(),
        "max".into(),
        Agents::new(format!("http://{addr}"), "test-qwen".into()).unwrap(),
    )
    .unwrap();
    let app = invoice_check::router(state);

    let long_history = "x".repeat(1301);
    let (status, body) = request(
        &app,
        "POST",
        "/api/chat",
        Some(json!({
            "question": "O que é uma nota fiscal?",
            "history": [
                {"role": "user", "content": "Explique em detalhes."},
                {"role": "assistant", "content": long_history}
            ]
        })),
    )
    .await;
    server.abort();

    assert_eq!(status, StatusCode::OK);
    let answer: Value = serde_json::from_slice(&body).unwrap();
    assert!(answer["invoice_id"].is_null());
    assert!(answer["revision"].is_null());
    assert_eq!(answer["uses_invoice_context"], false);
    assert_eq!(answer["evidence"], json!([]));
    assert_eq!(answer["provider"], "max");
    assert_eq!(answer["model"], "test-qwen");
}
#[tokio::test]
async fn general_chat_uses_validated_context_when_an_invoice_is_selected() {
    use axum::{Json, routing::post};

    let max = Router::new().route(
        "/v1/chat/completions",
        post(|Json(body): Json<Value>| async move {
            assert!(
                !body["messages"][1]["content"]
                    .as_str()
                    .unwrap()
                    .contains("SECOND-INVOICE-SECRET")
            );
            let payload: Value =
                serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
            let conversation = payload["conversation_history"].as_array().unwrap();
            assert!(
                conversation.is_empty()
                    || (conversation.len() == 2
                        && conversation[1]["content"].as_str().unwrap().chars().count() < 3000)
            );
            assert!(
                payload["invoice_context"]["evidence_catalog"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|item| item["id"] == "invoice:total" && item["value"] == "1001.79")
            );
            let content = json!({
                "answer": "O total extraído é 1001,79.",
                "cannot_answer": false,
                "uses_invoice_context": true,
                "evidence_ids": ["invoice:total"]
            })
            .to_string();
            Json(json!({
                "choices": [{"finish_reason": "stop", "message": {"content": content}}]
            }))
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, max).await.unwrap();
    });
    let state = AppState::new(
        root(),
        "max".into(),
        Agents::new(format!("http://{addr}"), "test-qwen".into()).unwrap(),
    )
    .unwrap();
    let record = insert_checked_record(&state);
    let mut other = Record::new("SECOND-INVOICE-SECRET.pdf".into());
    other.status = "done".into();
    other.result = record.result.clone();
    state.store.insert(&other, &first().1).unwrap();
    let before = serde_json::to_value(state.store.get(&record.id).unwrap().unwrap()).unwrap();
    let app = invoice_check::router(state.clone());

    let long_user = "u".repeat(1000);
    let long_answer = "🧾".repeat(3000);
    let (status, body) = request(
        &app,
        "POST",
        "/api/chat",
        Some(json!({
            "invoice_id": record.id,
            "revision": record.revision,
            "question": "Qual é o total?",
            "history": [
                {"role": "user", "content": long_user},
                {"role": "assistant", "content": long_answer}
            ]
        })),
    )
    .await;
    server.abort();

    assert_eq!(status, StatusCode::OK);
    let answer: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(answer["invoice_id"], record.id);
    assert_eq!(answer["revision"], record.revision);
    assert_eq!(answer["uses_invoice_context"], true);
    assert_eq!(answer["evidence"][0]["id"], "invoice:total");
    let after = serde_json::to_value(state.store.get(&record.id).unwrap().unwrap()).unwrap();
    assert_eq!(after, before);
}
#[tokio::test]
async fn invoice_chat_is_unavailable_in_demo_without_calling_max() {
    use axum::{Json, routing::post};
    use std::sync::atomic::{AtomicUsize, Ordering};

    let calls = Arc::new(AtomicUsize::new(0));
    let observed = calls.clone();
    let max = Router::new().route(
        "/v1/chat/completions",
        post(move || {
            let observed = observed.clone();
            async move {
                observed.fetch_add(1, Ordering::SeqCst);
                Json(json!({"choices":[]}))
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, max).await.unwrap();
    });
    let state = AppState::new(
        root(),
        "demo".into(),
        Agents::new(format!("http://{addr}"), "test-qwen".into()).unwrap(),
    )
    .unwrap();
    let record = insert_checked_record(&state);
    let app = invoice_check::router(state);

    let (general_status, general_body) = request(
        &app,
        "POST",
        "/api/chat",
        Some(json!({"question": "Can you help me?"})),
    )
    .await;
    assert_eq!(general_status, StatusCode::SERVICE_UNAVAILABLE);
    assert!(
        serde_json::from_slice::<Value>(&general_body).unwrap()["error"]
            .as_str()
            .unwrap()
            .contains("unavailable in demo mode")
    );

    let (status, body) = request(
        &app,
        "POST",
        &format!("/api/invoices/{}/chat", record.id),
        Some(json!({
            "revision": record.revision,
            "question": "What is the invoice total?"
        })),
    )
    .await;
    server.abort();

    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(
        serde_json::from_slice::<Value>(&body).unwrap()["error"]
            .as_str()
            .unwrap()
            .contains("unavailable in demo mode")
    );
}
#[tokio::test]
async fn invoice_chat_returns_grounded_answer_without_mutating_record() {
    use axum::{Json, routing::post};

    let max = Router::new().route(
        "/v1/chat/completions",
        post(|Json(body): Json<Value>| async move {
            assert_eq!(body["model"], "test-qwen");
            assert_eq!(body["max_tokens"], 768);
            assert_eq!(body["stream"], false);
            assert!(
                body["messages"][0]["content"]
                    .as_str()
                    .unwrap()
                    .starts_with("You are Invoice Check's local invoice assistant")
            );
            let payload: Value =
                serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
            assert_eq!(payload["question"], "What is the invoice total?");
            assert!(
                !body["messages"][1]["content"]
                    .as_str()
                    .unwrap()
                    .contains("SECOND-INVOICE-SECRET")
            );
            assert!(
                payload["evidence_catalog"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|item| item["id"] == "invoice:total" && item["value"] == "1001.79")
            );
            let content = json!({
                "answer": "The invoice total is USD 1001.79.",
                "cannot_answer": false,
                "evidence_ids": ["invoice:total"]
            })
            .to_string();
            Json(json!({
                "choices": [{"finish_reason": "stop", "message": {"content": content}}]
            }))
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, max).await.unwrap();
    });
    let state = AppState::new(
        root(),
        "max".into(),
        Agents::new(format!("http://{addr}"), "test-qwen".into()).unwrap(),
    )
    .unwrap();
    let record = insert_checked_record(&state);
    let mut other = Record::new("SECOND-INVOICE-SECRET.pdf".into());
    other.status = "done".into();
    other.result = record.result.clone();
    state.store.insert(&other, &first().1).unwrap();
    let before = serde_json::to_value(state.store.get(&record.id).unwrap().unwrap()).unwrap();
    let app = invoice_check::router(state.clone());

    let (status, body) = request(
        &app,
        "POST",
        &format!("/api/invoices/{}/chat", record.id),
        Some(json!({
            "revision": record.revision,
            "question": "What is the invoice total?"
        })),
    )
    .await;
    server.abort();

    assert_eq!(status, StatusCode::OK);
    let answer: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(answer["invoice_id"], record.id);
    assert_eq!(answer["revision"], record.revision);
    assert_eq!(answer["answer"], "The invoice total is USD 1001.79.");
    assert_eq!(answer["cannot_answer"], false);
    assert_eq!(answer["provider"], "max");
    assert_eq!(answer["model"], "test-qwen");
    assert_eq!(answer["evidence"][0]["id"], "invoice:total");
    assert_eq!(answer["evidence"][0]["value"], "1001.79");
    let after = serde_json::to_value(state.store.get(&record.id).unwrap().unwrap()).unwrap();
    assert_eq!(after, before);
}
#[tokio::test]
async fn invoice_chat_rejects_an_invalid_evidence_id() {
    use axum::{Json, routing::post};

    let max = Router::new().route(
        "/v1/chat/completions",
        post(|| async {
            let content = json!({
                "answer": "The invoice was paid yesterday.",
                "cannot_answer": false,
                "evidence_ids": ["payment:invented"]
            })
            .to_string();
            Json(json!({
                "choices": [{"finish_reason": "stop", "message": {"content": content}}]
            }))
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, max).await.unwrap();
    });
    let state = AppState::new(
        root(),
        "max".into(),
        Agents::new(format!("http://{addr}"), "test-qwen".into()).unwrap(),
    )
    .unwrap();
    let record = insert_checked_record(&state);
    let app = invoice_check::router(state);

    let (status, body) = request(
        &app,
        "POST",
        &format!("/api/invoices/{}/chat", record.id),
        Some(json!({
            "revision": record.revision,
            "question": "Has this invoice already been paid?"
        })),
    )
    .await;
    server.abort();

    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap()["error"],
        "The local invoice assistant returned unsupported evidence."
    );
}
#[tokio::test]
async fn max_reader_and_verifier_use_real_http_contract() {
    use axum::{Json, routing::post};
    let (ex, _, text) = first();
    let fixture = ex.clone();
    let app = Router::new().route(
        "/v1/chat/completions",
        post(move |Json(body): Json<Value>| {
            let fixture = fixture.clone();
            async move {
                assert_eq!(body["model"], "test-qwen");
                assert_eq!(body["max_tokens"], 2048);
                assert_eq!(body["stream"], false);
                let system = body["messages"][0]["content"].as_str().unwrap();
                let content = if system.starts_with("You transcribe") {
                    serde_json::to_string(&fixture).unwrap()
                } else {
                    "{\"concerns\":[\"Test: manually verify the item description\"]}".into()
                };
                Json(json!({"choices":[{"finish_reason":"stop","message":{"content":content}}]}))
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let agents = Agents::new(format!("http://{addr}"), "test-qwen".into()).unwrap();
    let result = agents.check(&text, &Reference::default(), &[]).await;
    server.abort();
    assert_eq!(result.agent_runs.len(), 2);
    assert!(
        result.agent_runs.iter().all(|r| r.status == "completed"),
        "{:?}",
        result.agent_runs
    );
    assert_eq!(result.decision, "review");
    assert!(result.issues.iter().any(|i| i.code == "VERIFIER_CONCERN"));
    assert_eq!(result.extraction.unwrap().invoice_number, ex.invoice_number);
}
#[tokio::test]
async fn failed_verifier_preserves_reader_and_fails_closed() {
    use axum::{Json, routing::post};
    let (ex, _, text) = first();
    let app = Router::new().route(
        "/v1/chat/completions",
        post(move |Json(body): Json<Value>| {
            let ex = ex.clone();
            async move {
                let content = if body["messages"][0]["content"]
                    .as_str()
                    .unwrap()
                    .starts_with("You transcribe")
                {
                    serde_json::to_string(&ex).unwrap()
                } else {
                    "not json".into()
                };
                Json(json!({"choices":[{"finish_reason":"stop","message":{"content":content}}]}))
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let result = Agents::new(format!("http://{addr}"), "test-qwen".into())
        .unwrap()
        .check(&text, &Reference::default(), &[])
        .await;
    server.abort();
    assert!(result.extraction.is_some());
    assert_eq!(result.decision, "review");
    assert_eq!(result.agent_runs[1].status, "failed");
}
#[tokio::test]
async fn incomplete_reader_is_rejected() {
    use axum::{Json, routing::post};
    let app = Router::new().route(
        "/v1/chat/completions",
        post(|| async {
            Json(json!({"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}))
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let result = Agents::new(format!("http://{addr}"), "test-qwen".into())
        .unwrap()
        .check("some text", &Reference::default(), &[])
        .await;
    server.abort();
    assert!(result.extraction.is_none());
    assert_eq!(result.decision, "review");
    assert_eq!(result.agent_runs[0].status, "failed");
}
