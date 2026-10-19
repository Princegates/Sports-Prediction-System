#!/usr/bin/env node
/**
 * Runs after `vite build` (see package.json's "postbuild") -- Cloudflare
 * Pages' build command is just `npm run build`, so this needs no change
 * to that config to start running on every deploy.
 *
 * Why this exists: dist/index.html is `<div id="root"></div>` and nothing
 * else until React mounts and fetches data -- a crawler that never
 * executes JS (and even Googlebot's own first, cheap HTML-only pass,
 * before it decides a page is worth the costlier JS-rendering pass) sees
 * an empty page. This script boots a local static server over dist/,
 * opens each public route in a real headless browser, waits for the app
 * to render, and overwrites that route's HTML with the fully-rendered
 * DOM -- so the file Cloudflare actually serves has real content, title
 * and meta tags baked in, no JS execution required to see them. The app
 * still hydrates and takes over normally for an actual visitor; this only
 * changes what's there before JS runs.
 *
 * Deliberately scoped to the fixed static routes (ROUTES below), not the
 * hundreds of /predict/:slugId fixture-preview pages the sitemap also
 * lists: those change too often and there are too many to prerender in a
 * frontend build without blowing past Cloudflare Pages' build-time
 * budget. They're unchanged by this script -- still client-rendered,
 * same as every route not in ROUTES, falling back to the prerendered "/"
 * page's shell via _redirects (dist/index.html) until their own JS takes
 * over, same as any SPA fallback.
 *
 * Each route gets a bounded, best-effort wait for the page's own data
 * fetches (not an unbounded networkidle): if the backend happens to be
 * cold or unreachable during this build, the page still prerenders with
 * its real static copy (headline, nav, footer) and a loading/empty state
 * for whatever needed live data -- strictly better than today's empty
 * shell, never a hung or failed build over it. A failure on one route is
 * logged and skipped, not fatal to the others.
 */

import { createServer } from "node:http";
import { readFile, mkdir, writeFile, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, "..", "dist");

// Mirrors backend/scripts/generate_sitemap.py's STATIC_URLS -- the fixed
// public marketing pages, not the generated fixture-preview URLs.
const ROUTES = ["/", "/how-it-works", "/pricing", "/fixtures", "/responsible", "/cookie-policy", "/login", "/register"];

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".txt": "text/plain; charset=utf-8",
  ".xml": "application/xml",
  ".webmanifest": "application/manifest+json",
};

async function fileExists(p) {
  try {
    await stat(p);
    return true;
  } catch {
    return false;
  }
}

/** Static file server over dist/, with the same SPA fallback _redirects
 * gives Cloudflare: any path with no matching file serves index.html. */
function startServer() {
  return new Promise((resolve) => {
    const server = createServer(async (req, res) => {
      const reqPath = decodeURIComponent(req.url.split("?")[0]);
      let filePath = path.join(DIST, reqPath);
      if (!(await fileExists(filePath)) || (await stat(filePath)).isDirectory()) {
        filePath = path.join(DIST, "index.html");
      }
      try {
        const body = await readFile(filePath);
        const ext = path.extname(filePath);
        res.writeHead(200, { "Content-Type": CONTENT_TYPES[ext] || "application/octet-stream" });
        res.end(body);
      } catch {
        res.writeHead(404);
        res.end();
      }
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

async function main() {
  if (!(await fileExists(path.join(DIST, "index.html")))) {
    console.log("prerender: no dist/index.html -- skipping (did `vite build` run?)");
    return;
  }

  let chromium;
  try {
    ({ chromium } = require("playwright"));
  } catch {
    console.log("prerender: playwright not installed -- skipping (dist/ keeps the plain build output)");
    return;
  }

  const server = await startServer();
  const { port } = server.address();
  const browser = await chromium.launch();

  try {
    const page = await browser.newPage();
    for (const route of ROUTES) {
      try {
        await page.goto(`http://127.0.0.1:${port}${route}`, { waitUntil: "load", timeout: 20_000 });
        // Best-effort settle for the route's own data fetch -- bounded, so
        // a slow or cold backend during this build delays this one route
        // by at most this long, never hangs the build.
        await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => {});
        const html = await page.content();

        const outDir = route === "/" ? DIST : path.join(DIST, route.slice(1));
        await mkdir(outDir, { recursive: true });
        await writeFile(path.join(outDir, "index.html"), html);
        console.log(`prerender: ${route} -> ${path.relative(DIST, path.join(outDir, "index.html")) || "index.html"}`);
      } catch (err) {
        console.warn(`prerender: ${route} failed, leaving its build output as-is -- ${err instanceof Error ? err.message : err}`);
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((err) => {
  // Never fail the build over this -- the plain Vite output (today's
  // behavior) is always a safe fallback.
  console.warn("prerender: unexpected error, leaving dist/ as the plain build output --", err);
});
