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
 * Covers the fixed static routes (ROUTES below), the biggest leagues' hub
 * pages (HUB_LEAGUES), plus the /predict/:slugId
 * preview pages for the next few days' fixtures only (upcomingFixtures
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

// League hub pages (/predictions/<league>) prerendered per build: the
// biggest competitions, which is where the search volume is. Mirrors
// src/lib/slug.ts's leagueSlug.
const HUB_LEAGUES = [
  "English Premier League", "Spanish La Liga", "Italian Serie A", "German Bundesliga", "French Ligue 1",
  "UEFA Champions League", "UEFA Europa League", "English Championship", "Dutch Eredivisie",
  "Portuguese Primeira Liga",
];

const API = (process.env.VITE_API_URL || "").replace(/\/+$/, "");

/** The next few days' fixtures, read from the public API at build time.
 * Best-effort like everything here: no API URL, a cold or unreachable
 * backend, or a bad response just means no fixture pages this build. */
async function upcomingFixtures() {
  if (!API) return [];
  try {
    const response = await fetch(
      `${API}/api/public/fixtures?days_ahead=${FIXTURE_DAYS_AHEAD}&limit=${MAX_FIXTURE_PAGES}`,
      { signal: AbortSignal.timeout(60_000) },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (err) {
    console.warn(`prerender: couldn't list upcoming fixtures (${err instanceof Error ? err.message : err}) -- skipping fixture pages`);
    return [];
  }
}

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/** The 1200x630 share card for one fixture -- what WhatsApp and X show for
 * a shared fixture link. Rendered by the same headless browser and saved
 * as a PNG beside the page. */
function shareCardHtml(f) {
  const crest = (url, name) =>
    url
      ? `<img src="${escapeHtml(url)}" alt="" style="width:150px;height:150px;object-fit:contain">`
      : `<div style="width:150px;height:150px;border-radius:50%;background:#2c2d33;color:#fff;display:flex;align-items:center;justify-content:center;font:700 56px Inter,Arial,sans-serif">${escapeHtml(name.slice(0, 2).toUpperCase())}</div>`;
  // The API's kickoffs are naive UTC; read them as UTC whatever the build
  // machine's own time zone is.
  const iso = /(Z|[+-]\d\d:?\d\d)$/.test(f.kickoff) ? f.kickoff : `${f.kickoff}Z`;
  const kickoff = new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  const tone = { HIGH: "#22c55e", MEDIUM: "#f59e0b", LOW: "#ef4444" }[f.confidence] || "#7c7e88";
  return `<!doctype html><html><body style="margin:0;width:1200px;height:630px;background:#15161a;color:#f4f4f6;font-family:Inter,Arial,sans-serif;display:flex;flex-direction:column;justify-content:space-between;padding:56px 64px;box-sizing:border-box">
  <div style="display:flex;justify-content:space-between;align-items:center;font-size:28px;color:#a1a3ad"><span>${escapeHtml(f.league)}</span><span>${escapeHtml(kickoff)} GMT</span></div>
  <div style="display:flex;align-items:center;justify-content:space-between;gap:24px">
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:20px;text-align:center">${crest(f.home_logo_url, f.home_team)}<span style="font:700 40px Georgia,serif">${escapeHtml(f.home_team)}</span></div>
    <span style="font:700 44px Georgia,serif;color:#7c7e88">vs</span>
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:20px;text-align:center">${crest(f.away_logo_url, f.away_team)}<span style="font:700 40px Georgia,serif">${escapeHtml(f.away_team)}</span></div>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:center">
    <span style="font:700 34px Georgia,serif"><span style="background:#ff5f1f;color:#fff;border-radius:10px;padding:4px 12px;margin-right:14px">SI</span>Socca Intelligence</span>
    ${f.has_prediction ? `<span style="border:3px solid ${tone};color:${tone};border-radius:999px;padding:8px 24px;font-size:28px;font-weight:700">${escapeHtml(f.confidence)} CONFIDENCE</span>` : ""}
  </div></body></html>`;
}

async function writeShareCard(browser, f, slug) {
  const card = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  try {
    await card.setContent(shareCardHtml(f), { waitUntil: "load", timeout: 15_000 });
    const out = path.join(DIST, "og", "predict", `${slug}.png`);
    await mkdir(path.dirname(out), { recursive: true });
    await card.screenshot({ path: out });
    return `https://soccaintel.com/og/predict/${slug}.png`;
  } finally {
    await card.close();
  }
}

/** Points a prerendered page's og:image/twitter:image at its own card. */
function withShareImage(html, url) {
  return html
    .replace(/(<meta property="og:image" content=")[^"]*(")/, `$1${url}$2`)
    .replace(/(<meta name="twitter:image" content=")[^"]*(")/, `$1${url}$2`);
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
    const fixtures = await upcomingFixtures();
    const fixtureBySlug = new Map(fixtures.map((f) => [fixtureSlug(f), f]));
    const routes = [
      ...ROUTES,
      ...HUB_LEAGUES.map((l) => `/predictions/${slugifyWord(l)}`),
      ...[...fixtureBySlug.keys()].map((slug) => `/predict/${slug}`),
    ];
    for (const route of routes) {
      try {
        await page.goto(`http://127.0.0.1:${port}${route}`, { waitUntil: "load", timeout: 20_000 });
        // Best-effort settle for the route's own data fetch -- bounded, so
        // a slow or cold backend during this build delays this one route
        // by at most this long, never hangs the build.
        await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => {});
        let html = await page.content();

        // A league hub the backend didn't know about at build time (cold API,
        // league not imported yet) renders "not found"; baking that would
        // serve it to crawlers even after the league arrives. Leave the plain
        // SPA shell instead, which renders the hub live.
        if (route.startsWith("/predictions/") && (await page.title()).startsWith("League not found")) {
          console.log(`prerender: ${route} has no data yet -- left to render live`);
          continue;
        }

        const fixture = route.startsWith("/predict/") ? fixtureBySlug.get(route.slice("/predict/".length)) : null;
        if (fixture) {
          try {
            html = withShareImage(html, await writeShareCard(browser, fixture, route.slice("/predict/".length)));
          } catch (err) {
            console.warn(`prerender: share card for ${route} failed, keeping the site card -- ${err instanceof Error ? err.message : err}`);
          }
        }

        // "/pricing" -> pricing.html, not pricing/index.html: Cloudflare
        // Pages serves a directory index only at "/pricing/" and 308s the
        // bare path there, so every sitemap URL, internal link and canonical
        // tag (all slash-less) pointed at a redirect. A same-named .html
        // file is served at "/pricing" itself.
        const outFile = route === "/" ? path.join(DIST, "index.html") : path.join(DIST, `${route.slice(1)}.html`);
        await mkdir(path.dirname(outFile), { recursive: true });
        await writeFile(outFile, html);
        console.log(`prerender: ${route} -> ${path.relative(DIST, outFile)}`);
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
