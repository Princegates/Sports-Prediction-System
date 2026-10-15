import { useEffect } from "react";

// True once any page has taken ownership of document.title via
// usePageMeta. main.tsx fires an unawaited branding fetch before React even
// mounts; if it resolves *after* a page's own usePageMeta effect has already
// run, it must not clobber that page's specific title back to the generic
// site name -- this flag is how it knows not to.
let pageTitleActive = false;

export function isPageTitleActive(): boolean {
  return pageTitleActive;
}

function upsertMetaDescription(content: string): void {
  let tag = document.querySelector('meta[name="description"]');
  if (!tag) {
    tag = document.createElement("meta");
    tag.setAttribute("name", "description");
    document.head.appendChild(tag);
  }
  tag.setAttribute("content", content);
}

/** Every public page shares one index.html (client-side routing), so
 * without an explicit canonical link a crawler has no signal that
 * /fixtures and /predict/arsenal-vs-chelsea-42 are different pages worth
 * indexing separately rather than duplicates of the same document. Query
 * strings are stripped -- this site has none that change a page's content
 * (no ?page=, no tracking params it reads), so the bare path is always the
 * canonical form. */
function upsertCanonicalLink(pathname: string): void {
  let tag = document.querySelector('link[rel="canonical"]');
  if (!tag) {
    tag = document.createElement("link");
    tag.setAttribute("rel", "canonical");
    document.head.appendChild(tag);
  }
  tag.setAttribute("href", `https://soccaintel.com${pathname}`);
}

/** Sets this page's document title, meta description and canonical link --
 * what a search-result snippet is built from, plus which URL it should
 * attach to. Each of the indexed public pages (see public/sitemap.xml)
 * calls this with its own specific copy via PublicShell; everything under
 * /app/* is disallowed in robots.txt and doesn't need one. */
export function usePageMeta(title: string, description: string): void {
  useEffect(() => {
    pageTitleActive = true;
    document.title = title;
    upsertMetaDescription(description);
    upsertCanonicalLink(window.location.pathname);
  }, [title, description]);
}

const STRUCTURED_DATA_ID = "page-structured-data";

function upsertStructuredData(data: Record<string, unknown> | null): void {
  const existing = document.getElementById(STRUCTURED_DATA_ID);
  if (!data) {
    existing?.remove();
    return;
  }
  let tag = existing;
  if (!tag) {
    tag = document.createElement("script");
    tag.id = STRUCTURED_DATA_ID;
    tag.setAttribute("type", "application/ld+json");
    document.head.appendChild(tag);
  }
  // .textContent, never innerHTML -- same reason the rest of this codebase
  // has no dangerouslySetInnerHTML (see public/_headers' own audit note):
  // this is plain text assignment, not markup parsing, so there's nothing
  // for an attacker-controlled team/league name to break out of. Verified
  // directly (not assumed) that this site's CSP -- script-src 'self', no
  // 'unsafe-inline' -- does NOT block an inline application/ld+json
  // script: Chrome only governs script-src for elements it treats as
  // executable script, and a non-executable type attribute like this one
  // isn't one. An inline <script> with no type (or type="text/javascript")
  // on the same page IS blocked, confirmed against the same header.
  tag.textContent = JSON.stringify(data);
}

/** schema.org structured data for this page -- Organization on the
 * homepage, SportsEvent on each fixture preview (see Welcome.tsx and
 * FixturePreview.tsx for what each actually sends). Only ever built from
 * real values already shown elsewhere on the page; never a field this
 * system doesn't actually have data for (no fabricated venue, no invented
 * odds). Removed on unmount so navigating to a page that doesn't call this
 * doesn't leave a stale, no-longer-true block behind. */
export function usePageStructuredData(data: Record<string, unknown> | null): void {
  // eslint-disable-next-line react-hooks/exhaustive-deps -- data is an object
  // literal rebuilt every render; comparing its JSON is the actual dependency.
  useEffect(() => {
    upsertStructuredData(data);
    return () => upsertStructuredData(null);
  }, [JSON.stringify(data)]);
}
