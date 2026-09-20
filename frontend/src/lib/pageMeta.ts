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

/** Sets this page's document title and meta description -- the two things
 * a search-result snippet is actually built from. Each of the indexed
 * public pages (see public/sitemap.xml) calls this with its own specific
 * copy via PublicShell; everything under /app/* is disallowed in
 * robots.txt and doesn't need one. */
export function usePageMeta(title: string, description: string): void {
  useEffect(() => {
    pageTitleActive = true;
    document.title = title;
    upsertMetaDescription(description);
  }, [title, description]);
}
