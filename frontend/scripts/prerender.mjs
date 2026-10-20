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
 * Covers the fixed static routes (ROUTES below) plus the /predict/:slugId
 * preview pages for the next few days' fixtures only (upcomingFixtureRoutes
 * -- at most MAX_FIXTURE_PAGES), not the hundreds the sitemap lists: those
 * nearest fixtures are the ones people are searching for right now, and a
 * bounded handful keeps the build inside Cloudflare Pages' time budget.
 * Every other fixture page stays client-rendered, falling back to the
 * prerendered "/" page's shell via _redirects (dist/index.html) until its
 * own JS takes over, same as any SPA fallback. A deploy each day (the
 * scheduled refresh) keeps the prerendered set current.
 *
 * Each route gets a bounded, best-effort wait for the page's own data
 * fetches (not an unbounded networkidle): if the backend happens to be
 * cold or unreachable during this build, the page still prerenders with
 * its real static copy (headline, nav, footer) and a loading/empty state
 * for whatever needed live data -- strictly better than today's empty
 * shell, never a hung or failed build over it. A failure on one route is
 * logged and skipped, not fatal to the others.
 *
 * Chromium itself is installed here, on demand, rather than left to
 * `playwright`'s own automatic postinstall download -- that download runs
 * during `npm install`, *before* this script (or any try/catch in it) gets
 * a chance to run, so a flaky or blocked download there would fail the
 * entire install and take the whole deploy down with it, not just skip
 * prerendering. Cloudflare Pages' project settings should set
 * PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 so that automatic download never even
 * attempts to run (see DEPLOYMENT.md); this script then fetches the browser
 * itself, inside the same try/catch that already guards the rest of
 * prerendering, so a failed download here degrades to the plain build
 * output instead of failing the build.
 */

import { createServer } from "node:http";
import { readFile, mkdir, writeFile, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, "..", "dist");

// Mirrors backend/scripts/generate_sitemap.py's STATIC_URLS -- the fixed
// public marketing pages, not the generated fixture-preview URLs.
const ROUTES = ["/", "/how-it-works", "/pricing", "/fixtures", "/responsible", "/cookie-policy", "/login", "/register"];

// Fixture preview pages prerendered per build -- see the header comment.
const FIXTURE_DAYS_AHEAD = 3;
const MAX_FIXTURE_PAGES = 20;

// Mirrors src/lib/slug.ts's fixtureSlug, so prerendered paths are exactly
// the URLs the app links to.
function slugifyWord(s) {
  return s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function fixtureSlug(f) {
  const teams = `${slugifyWord(f.home_team)}-vs-${slugifyWord(f.away_team)}`;
  return teams ? `${f.match_id}-${teams}` : String(f.match_id);
}

/** /predict/... routes for the next few days' fixtures, read from the
 * public API at build time. Best-effort like everything here: no API URL,
 * a cold or unreachable backend, or a bad response just means no fixture
 * pages this build. */
async function upcomingFixtureRoutes() {
  const api = (process.env.VITE_API_URL || "").replace(/\/+$/, "");
  if (!api) return [];
  try {
    const response = await fetch(
      `${api}/api/public/fixtures?days_ahead=${FIXTURE_DAYS_AHEAD}&limit=${MAX_FIXTURE_PAGES}`,
      { signal: AbortSignal.timeout(60_000) },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const fixtures = await response.json();
    return fixtures.map((f) => `/predict/${fixtureSlug(f)}`);
  } catch (err) {
    console.warn(`prerender: couldn't list upcoming fixtures (${err instanceof Error ? err.message : err}) -- skipping fixture pages`);
    return [];
  }
}

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

  let chromium, cliPath;
  try {
    ({ chromium } = require("playwright"));
    cliPath = path.join(path.dirname(require.resolve("playwright/package.json")), "cli.js");
  } catch {
    console.log("prerender: playwright not installed -- skipping (dist/ keeps the plain build output)");
    return;
  }

  try {
    execFileSync(process.execPath, [cliPath, "install", "chromium"], {
      stdio: "inherit",
      timeout: 120_000,
    });
  } catch (err) {
    console.warn(
      `prerender: couldn't install chromium (${err instanceof Error ? err.message : err}) -- skipping (dist/ keeps the plain build output)`,
    );
    return;
  }

  const server = await startServer();
  const { port } = server.address();
  const browser = await chromium.launch();

  try {
    const page = await browser.newPage();
    const routes = [...ROUTES, ...(await upcomingFixtureRoutes())];
    for (const route of routes) {
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
