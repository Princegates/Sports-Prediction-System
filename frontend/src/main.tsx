import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./lib/AuthContext";
import { readStoredAccent } from "./lib/accentProfiles";
import "./styles.css";

// Applied here (not just inside AppShell) so a hard reload landing directly
// on /login or /register still reflects the visitor's last theme/accent
// choice instead of resetting to the defaults.
try {
  const theme = localStorage.getItem("theme");
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
} catch {
  // private-browsing / storage-disabled -- defaults apply
}
document.documentElement.setAttribute("data-accent", readStoredAccent());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
