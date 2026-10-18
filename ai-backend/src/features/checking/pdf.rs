pub fn text(bytes: &[u8]) -> anyhow::Result<String> {
    anyhow::ensure!(bytes.starts_with(b"%PDF-"), "Upload a PDF document");
    let doc = lopdf::Document::load_mem(bytes)
        .map_err(|_| anyhow::anyhow!("The PDF is damaged or encrypted. Request a new copy."))?;
    anyhow::ensure!(!doc.is_encrypted(), "Encrypted PDFs require manual review");
    let pages = doc.get_pages();
    anyhow::ensure!(
        !pages.is_empty() && pages.len() <= 5,
        "Only PDFs with 1–5 pages are supported"
    );
    // Validate every page, including scans hidden among readable pages.
    for page in pages.keys() {
        let s = doc.extract_text(&[*page]).unwrap_or_default();
        anyhow::ensure!(
            s.chars().filter(|c| !c.is_whitespace()).count() >= 40,
            "A page has no readable text. Request a digital PDF or review manually."
        );
    }
    let text = pdf_extract::extract_text_from_mem(bytes)
        .map_err(|_| anyhow::anyhow!("Could not read the PDF text layer"))?;
    anyhow::ensure!(
        text.len() <= 24_000,
        "Invoice text exceeds the supported context. Review the complete document manually."
    );
    Ok(text)
}
