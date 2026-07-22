// Browser smoke test for the bench pages (the real AudioWorklet path, which
// the Node offline suite cannot exercise):
//   node web/test/browser-smoke.mjs
// For every page in boards.json plus the hub: load it, fail on any console
// error, click POWER, and require the power lamp to light - the lamp only
// turns on after the wasm worklet is created and the AudioContext is
// running. Also drives resonator/selftest.html and checks its offline
// render completes. Requires playwright (chromium).
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, dirname, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const webDir = dirname(dirname(fileURLToPath(import.meta.url)));
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".wasm": "application/wasm",
};

const server = createServer(async (req, res) => {
  try {
    let p = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (p.endsWith("/")) p += "index.html";
    const body = await readFile(join(webDir, p));
    res.writeHead(200, { "content-type": MIME[extname(p)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const base = `http://127.0.0.1:${server.address().port}`;

const { boards } = JSON.parse(await readFile(join(webDir, "boards.json"), "utf8"));
const pages = ["/", ...boards.map((b) => `/${b.id}/`)];

const browser = await chromium.launch({
  args: ["--autoplay-policy=no-user-gesture-required"],
});
let failures = 0;
const check = (label, ok, detail) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${detail ? `  (${detail})` : ""}`);
  if (!ok) failures++;
};

for (const path of pages) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto(base + path, { waitUntil: "load" });
  await page.waitForTimeout(400);

  const power = page.locator("#power");
  if (await power.count()) {
    await power.click();
    try {
      await page.locator("#power-lamp.on").waitFor({ timeout: 8000 });
    } catch {
      errors.push("power lamp never lit (worklet or AudioContext failed)");
    }
  }
  check(`page ${path}`, errors.length === 0, errors[0] ?? "");
  await page.close();
}

// resonator offline selftest: numeric render inside the browser
{
  const page = await browser.newPage();
  await page.goto(`${base}/resonator/selftest.html`, { waitUntil: "load" });
  try {
    await page.getByText("SELFTEST DONE").waitFor({ timeout: 30000 });
    const body = await page.locator("body").innerText();
    const peaks = /SELFTEST rldr .*peaks (\[.*?\])/.exec(body)?.[1];
    check("resonator in-browser offline render", true, peaks ?? "done");
  } catch {
    check("resonator in-browser offline render", false, "no SELFTEST DONE");
  }
  await page.close();
}

await browser.close();
server.close();
console.log(failures ? `\n${failures} page(s) failed` : "\nall pages passed");
process.exit(failures ? 1 : 0);
