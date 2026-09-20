import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./lib/AuthContext";
import { fetchBranding } from "./api";
import { isPageTitleActive } from "./lib/pageMeta";
import { THEME_OVERRIDE_KEY } from "./components/ThemeToggle";
import "./styles.css";

// Accent is admin-controlled, site-wide, with no personal override. Theme
// (day/night) is: a visitor's own choice, made with ThemeToggle on any
// public page, is stored under THEME_OVERRIDE_KEY and always wins over the
// admin's site-wide default once set -- checked first here, and again
// after the branding fetch below so a slow response can't clobber it.
//
// A cached copy of the last-fetched branding applies immediately regardless
// (avoids a flash of the compiled-in default on repeat visits, and covers a
// hard reload landing directly on /login before the fetch below resolves);
// the live fetch then wins over *that*, since the admin default may have
// changed since the cache was written -- but never over a personal choice.
try {
  const personalTheme = localStorage.getItem(THEME_OVERRIDE_KEY);
  const cachedTheme = personalTheme || localStorage.getItem("site_theme");
  if (cachedTheme) document.documentElement.setAttribute("data-theme", cachedTheme);
  const cachedAccent = localStorage.getItem("site_accent");
  if (cachedAccent) document.documentElement.setAttribute("data-accent", cachedAccent);
} catch {
  // private-browsing / storage-disabled -- compiled-in defaults apply
}

// Deliberately not awaited: a slow or failed call must not delay the first
// paint, and the cached/compiled-in defaults are a perfectly good answer
// until this resolves.
fetchBranding()
  .then((branding) => {
    let personalTheme: string | null = null;
    try {
      personalTheme = localStorage.getItem(THEME_OVERRIDE_KEY);
    } catch {
      // private-browsing / storage-disabled -- nothing to honor
    }
    document.documentElement.setAttribute("data-theme", personalTheme || branding.default_theme);
    document.documentElement.setAttribute("data-accent", branding.default_accent);
    // A page's own, more specific title (set via usePageMeta) must not be
    // overwritten by this generic one if it already applied.
    if (branding.site_name && !isPageTitleActive()) document.title = branding.site_name;
    try {
      localStorage.setItem("site_theme", branding.default_theme);
      localStorage.setItem("site_accent", branding.default_accent);
    } catch {
      // private-browsing / storage-disabled -- just won't cache for next visit
    }
  })
  .catch(() => {
    // Offline, or the API is down. The cached/compiled-in defaults already applied.
  });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
