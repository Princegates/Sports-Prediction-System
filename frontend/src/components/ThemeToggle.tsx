import { useEffect, useState } from "react";

export const THEME_OVERRIDE_KEY = "theme_override";

function currentTheme(): "dark" | "light" {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/** A visitor's own day/night choice, independent of the admin's site-wide
 * default theme (see main.tsx, which always applies this over the admin
 * default once it's been set, and never lets a later branding fetch
 * clobber it). Persisted so it survives a reload, and shared across public
 * pages (PublicShell) and the logged-in app (AppShell) -- one toggle,
 * one stored choice, wherever it's mounted. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">(currentTheme);

  // Reflects a toggle made in another tab for the same visitor.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== THEME_OVERRIDE_KEY) return;
      const next = currentTheme();
      document.documentElement.setAttribute("data-theme", next);
      setTheme(next);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_OVERRIDE_KEY, next);
    } catch {
      // private-browsing / storage-disabled -- still applies for this visit,
      // just won't be remembered for the next one.
    }
    setTheme(next);
  }

  const label = theme === "dark" ? "Switch to day mode" : "Switch to night mode";

  return (
    <button type="button" className="theme-toggle" onClick={toggle} aria-label={label} title={label}>
      {theme === "dark" ? "☀️" : "🌙"}
    </button>
  );
}
