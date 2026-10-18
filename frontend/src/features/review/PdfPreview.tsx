import { useEffect, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
GlobalWorkerOptions.workerSrc = workerUrl;
function Page({
  document,
  page,
}: {
  document: PDFDocumentProxy;
  page: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    let cancelRender: undefined | (() => void);
    void document
      .getPage(page)
      .then((pdfPage) => {
        if (cancelled || !canvas.current) return;
        const viewport = pdfPage.getViewport({ scale: 1.5 });
        canvas.current.width = viewport.width;
        canvas.current.height = viewport.height;
        const task = pdfPage.render({ canvas: canvas.current, viewport });
        cancelRender = () => task.cancel();
        void task.promise.catch((e) => {
          if (!cancelled) setError(e.message);
        });
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
      cancelRender?.();
    };
  }, [document, page]);
  return (
    <div className="pdf-page">
      <canvas ref={canvas} aria-label={`Invoice page ${page}`} />
      {error && (
        <p role="alert">Could not render this page. Open the original PDF.</p>
      )}
      <small>
        Page {page} of {document.numPages}
      </small>
    </div>
  );
}
export function PdfPreview({ id }: { id: string }) {
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setDocument(null);
    setError("");
    const loading = getDocument({ url: `/api/invoices/${id}/pdf` });
    void loading.promise
      .then((pdf) => {
        if (active) setDocument(pdf);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
      void loading.destroy();
    };
  }, [id]);
  return (
    <div className="pdf-preview">
      {error ? (
        <p role="alert">
          Could not preview the PDF. Use “Open original PDF” to inspect it.
        </p>
      ) : document ? (
        Array.from({ length: Math.min(document.numPages, 5) }, (_, i) => (
          <Page key={i} document={document} page={i + 1} />
        ))
      ) : (
        <p>Loading original invoice…</p>
      )}
      {document && document.numPages > 5 && (
        <p>
          This preview shows the first five pages. Open the original PDF to
          review the complete document.
        </p>
      )}
    </div>
  );
}
