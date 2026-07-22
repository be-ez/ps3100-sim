// Dev server for web/: static file serving with Cache-Control: no-store, so
// browsers always fetch the current build (python http.server sends only
// Last-Modified, and heuristic caching then serves stale app code after
// rewrites - the "new HTML + old JS" trap).
//   node web/tools/serve.mjs [port] [host]
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, dirname, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = dirname(dirname(fileURLToPath(import.meta.url)));
const port = Number(process.argv[2] ?? 8931);
const host = process.argv[3] ?? "0.0.0.0";

const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".wasm": "application/wasm",
  ".md": "text/plain", ".png": "image/png", ".svg": "image/svg+xml",
};

createServer(async (req, res) => {
  try {
    let p = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (p.endsWith("/")) p += "index.html";
    const body = await readFile(join(webDir, p));
    res.writeHead(200, {
      "content-type": MIME[extname(p)] ?? "application/octet-stream",
      "cache-control": "no-store",
    });
    res.end(body);
  } catch {
    res.writeHead(404, { "cache-control": "no-store" }).end("not found");
  }
}).listen(port, host, () => console.log(`serving ${webDir} on http://${host}:${port}/`));
