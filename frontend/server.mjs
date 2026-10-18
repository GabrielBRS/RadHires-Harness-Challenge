// Small production static server; API requests proxy to the separate Rust process.
import http from "node:http";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = path.resolve(fileURLToPath(new URL("./dist/", import.meta.url)));
const port = Number(process.env.FRONTEND_PORT || 3000);
const apiPort = Number(process.env.BACKEND_PORT || 8080);
const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};
http
  .createServer(async (req, res) => {
    if (req.url?.startsWith("/api/")) {
      if (!["GET", "HEAD", "OPTIONS"].includes(req.method || "GET")) {
        const allowed = new Set([
          `http://localhost:${port}`,
          `http://127.0.0.1:${port}`,
        ]);
        if (
          req.headers["sec-fetch-site"] === "cross-site" ||
          (req.headers.origin && !allowed.has(req.headers.origin))
        ) {
          res.writeHead(403);
          res.end("Cross-site requests are not allowed");
          return;
        }
      }
      const proxy = http.request(
        {
          hostname: "127.0.0.1",
          port: apiPort,
          path: req.url,
          method: req.method,
          headers: { ...req.headers, host: `127.0.0.1:${apiPort}` },
        },
        (upstream) => {
          res.writeHead(upstream.statusCode || 502, upstream.headers);
          upstream.pipe(res);
        },
      );
      const isChatRequest =
        /^\/api\/(?:chat(?:\?|$)|invoices\/[^/?]+\/chat(?:\?|$))/.test(req.url);
      proxy.setTimeout(isChatRequest ? 600_000 : 190_000, () =>
        proxy.destroy(new Error("API timeout")),
      );
      proxy.on("error", () => {
        if (!res.headersSent)
          res.writeHead(502, { "content-type": "application/json" });
        res.end(
          JSON.stringify({
            error: "The invoice service is unavailable. Restart run.py.",
          }),
        );
      });
      req.pipe(proxy);
      req.on("aborted", () => proxy.destroy());
      return;
    }
    if (!["GET", "HEAD"].includes(req.method || "GET")) {
      res.writeHead(405);
      res.end();
      return;
    }
    try {
      let pathname = decodeURIComponent(
        new URL(req.url, "http://localhost").pathname,
      );
      let file = path.resolve(root, `.${pathname}`);
      if (file !== root && !file.startsWith(root + path.sep)) {
        res.writeHead(403);
        res.end();
        return;
      }
      try {
        if (!(await stat(file)).isFile()) file = path.join(root, "index.html");
      } catch {
        file = path.join(root, "index.html");
      }
      res.writeHead(200, {
        "content-type": mime[path.extname(file)] || "application/octet-stream",
        "cache-control": file.endsWith("index.html")
          ? "no-cache"
          : "public, max-age=3600",
        "x-content-type-options": "nosniff",
      });
      if (req.method === "HEAD") res.end();
      else
        createReadStream(file)
          .on("error", () => res.destroy())
          .pipe(res);
    } catch {
      res.writeHead(400);
      res.end("Invalid request");
    }
  })
  .listen(port, "127.0.0.1", () =>
    console.log(`React UI on http://localhost:${port}`),
  );
