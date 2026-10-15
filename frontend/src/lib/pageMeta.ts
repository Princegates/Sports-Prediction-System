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
