import { useEffect, useState } from "react";

export const THEME_OVERRIDE_KEY = "theme_override";

function currentTheme(): "dark" | "light" {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/** A visitor's own day/night choice for the public pages, independent of
 * the admin's site-wide default theme (see main.tsx, which always applies
 * this over the admin default once it's been set, and never lets a later
 * branding fetch clobber it). Persisted so it survives a reload; the
 * logged-in app keeps the admin default only -- this is public-pages only,
 * same as the request that added it. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">(currentTheme);

  // Reflects a toggle made in another tab for the same visitor.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === THEME_OVERRIDE_KEY) setTheme(currentTheme());
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
