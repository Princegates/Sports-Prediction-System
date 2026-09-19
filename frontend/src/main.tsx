import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./lib/AuthContext";
import { fetchBranding } from "./api";
import "./styles.css";

// Theme and accent are admin-controlled, site-wide -- there is no personal
// override. A cached copy of the last-fetched branding applies immediately
// (avoids a flash of the compiled-in default on repeat visits, and covers a
// hard reload landing directly on /login before the fetch below resolves);
// the live fetch then always wins, since it may have changed since the cache
// was written.
try {
  const cachedTheme = localStorage.getItem("site_theme");
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
    document.documentElement.setAttribute("data-theme", branding.default_theme);
    document.documentElement.setAttribute("data-accent", branding.default_accent);
    if (branding.site_name) document.title = branding.site_name;
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
