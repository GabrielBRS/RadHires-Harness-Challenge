pub mod baseline;
pub mod models;
pub mod pdf;
use chrono::NaiveDate;
use models::*;
use std::collections::BTreeSet;

#[derive(Clone)]
pub struct Reference {
    pub pos: Vec<PurchaseOrder>,
    pub paid: Vec<PaidInvoice>,
}
impl Default for Reference {
    fn default() -> Self {
        Self {
            pos: serde_json::from_str(include_str!("../../../../data/purchase_orders.json"))
                .expect("valid PO fixtures"),
            paid: serde_json::from_str(include_str!("../../../../data/paid_invoices.json"))
                .expect("valid paid fixtures"),
        }
    }
}
pub fn alnum(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}
pub fn po_key(s: &str) -> String {
    s.chars()
        .filter(char::is_ascii_digit)
        .collect::<String>()
        .trim_start_matches('0')
        .into()
}
fn tokens(s: &str) -> BTreeSet<String> {
    s.to_lowercase()
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .collect()
}
fn supplier_similarity(a: &str, b: &str) -> f64 {
    let legal = [
        "llc",
        "inc",
        "co",
        "company",
        "corp",
        "corporation",
        "ltd",
        "the",
        "and",
    ];
    let a: BTreeSet<_> = tokens(a)
        .into_iter()
        .filter(|s| !legal.contains(&s.as_str()))
        .collect();
    let b: BTreeSet<_> = tokens(b)
        .into_iter()
        .filter(|s| !legal.contains(&s.as_str()))
        .collect();
    if a.is_empty() || b.is_empty() {
        return 0.;
    }
    let shared = a.intersection(&b).count() as f64;
    0.5 * shared / a.len().min(b.len()) as f64 + 0.5 * shared / a.union(&b).count() as f64
}
fn similarity(a: &str, b: &str) -> f64 {
    let a = tokens(a);
    let b = tokens(b);
    if a.is_empty() || b.is_empty() {
        return 0.;
    }
    let overlap = a.intersection(&b).count() as f64 / a.union(&b).count() as f64;
    overlap.max(strsim::normalized_levenshtein(
        &a.into_iter().collect::<Vec<_>>().join(" "),
        &b.into_iter().collect::<Vec<_>>().join(" "),
    ))
}
fn rounded(v: f64) -> f64 {
    (v * 100.).round() / 100.
}
fn date(s: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(s, "%Y-%m-%d").ok()
}

pub fn ungrounded(ex: &Invoice, text: &str) -> Vec<String> {
    let flat = text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    let squashed = alnum(text);
    let number_re = regex::Regex::new(r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?").unwrap();
    let numbers: Vec<f64> = number_re
        .find_iter(text)
        .filter_map(|m| m.as_str().replace(',', "").parse().ok())
        .collect();
    let mut missing = vec![];
    for (key, value) in [
        ("invoice_number", &ex.invoice_number),
        ("po_number", &ex.po_number),
    ] {
        if let Some(v) = value
            && (alnum(v).is_empty() || !squashed.contains(&alnum(v)))
        {
            missing.push(key.into());
        }
    }
    if let Some(v) = &ex.invoice_date {
        let found = date(v).is_some_and(|d| {
            [
                "%Y-%m-%d",
                "%B %-d, %Y",
                "%b %-d, %Y",
                "%m/%d/%Y",
                "%-m/%-d/%Y",
                "%d %B %Y",
                "%B %d, %Y",
            ]
            .iter()
            .any(|f| flat.contains(&d.format(f).to_string().to_lowercase()))
        });
        if !found {
            missing.push("invoice_date".into());
        }
    }
    let mut values: Vec<(String, Option<f64>)> = vec![
        ("subtotal".into(), ex.subtotal),
        ("tax".into(), ex.tax),
        ("total".into(), ex.total),
    ];
    for (i, l) in ex.lines.iter().enumerate() {
        if let Some(sku) = &l.sku
            && (!squashed.contains(&alnum(sku)) || alnum(sku).is_empty())
        {
            missing.push(format!("lines[{i}].sku"));
        }
        values.extend([
            (format!("lines[{i}].quantity"), Some(l.quantity)),
            (format!("lines[{i}].unit_price"), Some(l.unit_price)),
            (format!("lines[{i}].line_total"), l.line_total),
        ]);
    }
    for (key, v) in values {
        if let Some(v) = v
            && !numbers.iter().any(|n| (n - v).abs() < 0.00001)
        {
            missing.push(key);
        }
    }
    missing
}

pub fn check(
    ex: Invoice,
    text: &str,
    reference: &Reference,
    queued: &[CheckResult],
    extractor: &str,
) -> CheckResult {
    let mut issues = vec![];
    let missing = ex
        .supplier_name
        .as_deref()
        .is_none_or(|s| s.trim().is_empty())
        || ex
            .invoice_number
            .as_deref()
            .is_none_or(|s| s.trim().is_empty())
        || ex.invoice_date.is_none()
        || ex.subtotal.is_none()
        || ex.tax.is_none()
        || ex.total.is_none()
        || ex.lines.is_empty()
        || ex.lines.iter().any(|l| l.line_total.is_none());
    if missing {
        issues.push(issue(
            "MISSING_FIELD",
            "review",
            "Missing invoice information",
            "Check the supplier, invoice number, date, amounts and line items.",
        ));
    }
    if ex.currency.as_deref() != Some("USD")
        || ex.total.is_some_and(|n| n <= 0.)
        || [ex.subtotal, ex.tax].into_iter().flatten().any(|n| n < 0.)
        || ex
            .lines
            .iter()
            .any(|l| l.quantity <= 0. || l.unit_price < 0. || l.line_total.is_some_and(|n| n < 0.))
        || ex
            .invoice_date
            .as_deref()
            .is_some_and(|s| date(s).is_none())
    {
        issues.push(issue(
            "UNSUPPORTED_INVOICE",
            "review",
            "Manual processing required",
            "Only positive USD invoices with valid dates and quantities are supported.",
        ));
    }
    let ungrounded = ungrounded(&ex, text);
    if !ungrounded.is_empty() {
        issues.push(issue(
            "UNCONFIRMED_FIELDS",
            "review",
            "Values could not be confirmed on the page",
            &ungrounded.join(", "),
        ));
    }
    for (i, l) in ex.lines.iter().enumerate() {
        if let Some(total) = l.line_total {
            let expected = rounded(l.quantity * l.unit_price);
            if (total - expected).abs() > 0.020001 {
                let mut item = issue(
                    "MATH_ERROR",
                    "review",
                    "Line amount does not add up",
                    &format!(
                        "{} × ${:.2} = ${:.2}; printed ${:.2}.",
                        l.quantity, l.unit_price, expected, total
                    ),
                );
                item.line_index = Some(i);
                item.amount_at_stake = Some(rounded(total - expected));
                issues.push(item);
            }
        }
    }
    let sum: f64 = ex
        .lines
        .iter()
        .map(|l| l.line_total.unwrap_or(l.quantity * l.unit_price))
        .sum();
    if ex
        .subtotal
        .is_some_and(|v| (v - rounded(sum)).abs() > 0.020001)
    {
        issues.push(issue(
            "MATH_ERROR",
            "review",
            "Subtotal does not add up",
            &format!(
                "Lines total ${sum:.2}; printed subtotal ${:.2}.",
                ex.subtotal.unwrap()
            ),
        ));
    }
    if let (Some(s), Some(t), Some(total)) = (ex.subtotal, ex.tax, ex.total)
        && (s + t - total).abs() > 0.020001
    {
        issues.push(issue(
            "MATH_ERROR",
            "review",
            "Total does not add up",
            &format!("${s:.2} + ${t:.2} tax differs from ${total:.2}."),
        ));
    }
    let po = ex
        .po_number
        .as_ref()
        .filter(|_| !ungrounded.contains(&"po_number".into()))
        .and_then(|n| {
            reference
                .pos
                .iter()
                .find(|p| po_key(&p.po_number) == po_key(n))
        })
        .cloned();
    let supplier = reference
        .pos
        .iter()
        .map(|p| {
            (
                supplier_similarity(ex.supplier_name.as_deref().unwrap_or(""), &p.supplier_name),
                &p.supplier_id,
            )
        })
        .max_by(|a, b| a.0.total_cmp(&b.0))
        .filter(|p| p.0 >= 0.55)
        .map(|p| p.1.clone());
    if let Some(po) = &po {
        if supplier_similarity(ex.supplier_name.as_deref().unwrap_or(""), &po.supplier_name) < 0.55
        {
            issues.push(issue(
                "SUPPLIER_MISMATCH",
                "block",
                "PO belongs to another supplier",
                &format!("{} belongs to {}.", po.po_number, po.supplier_name),
            ));
        }
    } else {
        issues.push(issue(
            "PO_NOT_FOUND",
            "block",
            "No matching purchase order",
            "Confirm the purchase-order number with the supplier or purchasing.",
        ));
    }
    // The queue is evidence of an existing invoice, never evidence of a payment.
    let mut seen: Vec<(String, String, String, f64, &str)> = reference
        .paid
        .iter()
        .map(|p| {
            (
                p.supplier_id.clone(),
                p.invoice_number.clone(),
                p.invoice_date.clone(),
                p.total,
                "payment history",
            )
        })
        .collect();
    for q in queued {
        if let (Some(s), Some(e)) = (&q.supplier_id, &q.extraction)
            && let (Some(n), Some(d), Some(t)) = (&e.invoice_number, &e.invoice_date, e.total)
        {
            seen.push((s.clone(), n.clone(), d.clone(), t, "this session's queue"));
        }
    }
    let same_supplier: Vec<_> = seen
        .iter()
        .filter(|p| Some(&p.0) == supplier.as_ref())
        .collect();
    let duplicate = same_supplier.iter().find(|p| {
        !alnum(ex.invoice_number.as_deref().unwrap_or("")).is_empty()
            && alnum(&p.1) == alnum(ex.invoice_number.as_deref().unwrap_or(""))
    });
    if let Some(p) = duplicate {
        issues.push(issue(
            "DUPLICATE_INVOICE",
            "block",
            "Invoice already recorded",
            &format!(
                "Invoice {} is already in {}. Check before paying.",
                p.1, p.4
            ),
        ));
    } else if let Some(p) = same_supplier.iter().find(|p| {
        ex.total.is_some_and(|t| (p.3 - t).abs() < 0.01)
            && ex
                .invoice_date
                .as_deref()
                .and_then(date)
                .zip(date(&p.2))
                .is_some_and(|(a, b)| (a - b).num_days().abs() <= 14)
    }) {
        issues.push(issue(
            "POSSIBLE_DUPLICATE",
            "block",
            "Possible reissued invoice",
            &format!(
                "Same supplier and total as {} in {}, dated within 14 days.",
                p.1, p.4
            ),
        ));
    }
    if let Some(po) = &po {
        let mut used = BTreeSet::new();
        let mut matches = vec![None; ex.lines.len()];
        for (i, l) in ex.lines.iter().enumerate() {
            if let Some(p) = po.lines.iter().find(|p| {
                !used.contains(&p.line)
                    && l.sku
                        .as_ref()
                        .is_some_and(|s| !alnum(s).is_empty() && alnum(s) == alnum(&p.sku))
            }) {
                matches[i] = Some(p);
                used.insert(p.line);
            }
        }
        let mut pairs: Vec<_> = ex
            .lines
            .iter()
            .enumerate()
            .filter(|(i, _)| matches[*i].is_none())
            .flat_map(|(i, l)| {
                po.lines
                    .iter()
                    .map(move |p| (similarity(&l.description, &p.description), i, p))
            })
            .collect();
        pairs.sort_by(|a, b| b.0.total_cmp(&a.0));
        for (score, i, p) in pairs {
            if score >= 0.6 && matches[i].is_none() && !used.contains(&p.line) {
                matches[i] = Some(p);
                used.insert(p.line);
            }
        }
        for (i, l) in ex.lines.iter().enumerate() {
            let mut add =
                |code: &str, severity: &str, title: &str, detail: String, amount: Option<f64>| {
                    let mut it = issue(code, severity, title, &detail);
                    it.line_index = Some(i);
                    it.amount_at_stake = amount;
                    issues.push(it);
                };
            if let Some(p) = matches[i] {
                let diff = l.unit_price - p.unit_price;
                let limit = 0.01_f64.max(p.unit_price * 0.005);
                if diff > limit {
                    add(
                        "PRICE_VARIANCE",
                        "review",
                        "Price is above the agreed amount",
                        format!(
                            "{}: invoice ${:.2} each; PO ${:.2} each.",
                            l.description, l.unit_price, p.unit_price
                        ),
                        Some(rounded(diff * l.quantity)),
                    );
                } else if diff < -limit {
                    add(
                        "PRICE_BELOW_PO",
                        "info",
                        "Price is below the PO",
                        format!(
                            "{}: ${:.2} below the agreed unit price.",
                            l.description, -diff
                        ),
                        None,
                    );
                }
                let paid: f64 = if duplicate.is_some() {
                    0.
                } else {
                    reference
                        .paid
                        .iter()
                        .filter(|old| po_key(&old.po_number) == po_key(&po.po_number))
                        .flat_map(|old| &old.lines)
                        .filter(|old| alnum(&old.sku) == alnum(&p.sku))
                        .map(|old| old.quantity)
                        .sum()
                };
                let remaining = p.qty_received - paid;
                if l.quantity > remaining + 1e-9 {
                    add(
                        "QTY_EXCEEDS_RECEIVED",
                        "review",
                        "Billed for more than received",
                        format!(
                            "{}: billed {}; received {}; previously paid {}; left to bill {}.",
                            l.description,
                            l.quantity,
                            p.qty_received,
                            paid,
                            remaining.max(0.)
                        ),
                        Some(rounded((l.quantity - remaining.max(0.)) * l.unit_price)),
                    );
                }
            } else {
                add(
                    "ITEM_NOT_ON_PO",
                    "review",
                    "Charge is not on the purchase order",
                    l.description.clone(),
                    l.line_total,
                );
            }
        }
    }
    let mut result = CheckResult {
        extraction: Some(ex),
        issues,
        decision: "review".into(),
        supplier_id: supplier,
        po,
        agent_runs: vec![],
        extractor: extractor.into(),
    };
    result.decide();
    result
}
