import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./lib/AuthContext";
import { readStoredAccent } from "./lib/accentProfiles";
import { fetchBranding } from "./api";
import "./styles.css";

// Applied here (not just inside AppShell) so a hard reload landing directly
// on /login or /register still reflects the visitor's last theme/accent
// choice instead of resetting to the defaults.
let hasOwnTheme = false;
let hasOwnAccent = false;
try {
  const theme = localStorage.getItem("theme");
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
    hasOwnTheme = true;
  }
  hasOwnAccent = localStorage.getItem("accent_profile") !== null;
} catch {
  // private-browsing / storage-disabled -- defaults apply
}
document.documentElement.setAttribute("data-accent", readStoredAccent());

// The superadmin's chosen look, for anyone who hasn't picked their own. Fetched
// rather than bundled because it must be changeable without a rebuild, and
// applied only where the visitor has no preference of their own -- overriding
// someone's explicit choice with a site default would be a bug, not branding.
//
// Deliberately not awaited: a slow or failed call must not delay the first
// paint, and the compiled-in defaults are a perfectly good answer.
fetchBranding()
  .then((branding) => {
    if (!hasOwnTheme) document.documentElement.setAttribute("data-theme", branding.default_theme);
    if (!hasOwnAccent) document.documentElement.setAttribute("data-accent", branding.default_accent);
    if (branding.site_name) document.title = branding.site_name;
  })
  .catch(() => {
    // Offline, or the API is down. The bundled defaults already applied.
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
