use super::models::{Invoice, LineItem};
use regex::Regex;
fn find(pattern: &str, text: &str) -> Option<String> {
    Regex::new(pattern)
        .unwrap()
        .captures(text)
        .and_then(|c| c.get(1))
        .map(|s| s.as_str().trim().into())
}
fn number(s: Option<String>) -> Option<f64> {
    s.and_then(|s| s.replace(',', "").parse().ok())
}
pub fn extract(text: &str) -> anyhow::Result<Invoice> {
    let re=Regex::new(r"(?m)^\s*([A-Z]{1,4}-[A-Z0-9]+)\s+(.+?)\s+(\d+(?:\.\d+)?)(?:\s*(?:ea|pcs|units?))?\s+\$?\s?([\d,]+\.\d{2})\s+\$?\s?([\d,]+\.\d{2})\s*$").unwrap();
    let lines = re
        .captures_iter(text)
        .map(|c| LineItem {
            sku: Some(c[1].into()),
            description: c[2].into(),
            quantity: c[3].parse().unwrap(),
            unit_price: c[4].replace(',', "").parse().unwrap(),
            line_total: c[5].replace(',', "").parse().ok(),
        })
        .collect::<Vec<_>>();
    anyhow::ensure!(
        !lines.is_empty(),
        "The demo reader found no line-item table. Use the local AI reader or enter corrections manually."
    );
    Ok(Invoice {
        supplier_name: text
            .lines()
            .map(str::trim)
            .find(|s| !s.is_empty() && *s != "INVOICE")
            .map(String::from),
        invoice_number: find(
            r"(?im)invoice\s*(?:no\.?|number|#)\s*:?\s*([A-Z0-9][A-Z0-9-]+)",
            text,
        ),
        invoice_date: find(r"(?im)(?:invoice )?date:?\s*(\d{4}-\d{2}-\d{2})", text),
        po_number: find(
            r"(?im)P\.?O\.?\s*(?:number|no\.?|#)\s*:?\s*([A-Z0-9][A-Z0-9-]+)",
            text,
        ),
        currency: find(r"\b(USD|EUR|GBP|CAD|AUD|BRL)\b", text)
            .or_else(|| text.contains('$').then(|| "USD".into())),
        subtotal: number(find(r"(?im)subtotal\s*\$?\s?([\d,]+\.\d{2})", text)),
        tax: number(find(
            r"(?im)tax[^\n$\d]*(?:\(\d+%\))?\s*\$?\s?([\d,]+\.\d{2})",
            text,
        )),
        total: number(find(
            r"(?im)(?:total due|amount due|^total)\s*\$?\s?([\d,]+\.\d{2})",
            text,
        )),
        lines,
    })
}
