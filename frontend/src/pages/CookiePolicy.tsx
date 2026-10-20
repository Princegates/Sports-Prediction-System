import { useEffect, useState } from "react";
import { fetchBranding } from "../api";
import { PublicShell } from "../components/PublicShell";
import { whatsappLink } from "../lib/whatsapp";
import type { Branding } from "../types";

/**
 * Public Cookie Policy -- what this site actually stores in a visitor's
 * browser and why, kept to what's literally true rather than boilerplate
 * copied from a template.
 *
 * The honest version is unusually short: the backend never sends a
 * Set-Cookie header (api.ts/AuthContext authenticate with a bearer token
 * read from localStorage, not a cookie -- see config.py's own CORS comment),
 * and there is no analytics or advertising script anywhere in index.html or
 * the app (checked directly rather than assumed). Everything this page
 * describes is first-party localStorage, itemized from the actual
 * localStorage.setItem call sites across the app rather than a generic
 * "we may use cookies for various purposes" disclaimer -- inventing
 * categories nobody can verify would be exactly the kind of guess this
 * project avoids everywhere else.
 */

const STORAGE_ITEMS: { key: string; purpose: string; necessary: boolean }[] = [
  { key: "auth_token", purpose: "Keeps you signed in between visits.", necessary: true },
  { key: "cookie_notice_ack", purpose: "Remembers that you've dismissed this notice.", necessary: true },
  { key: "theme_override", purpose: "Your personal light/dark choice, overriding the site default.", necessary: false },
  { key: "site_theme", purpose: "A cached copy of the site's default light/dark theme, for instant loading.", necessary: false },
  { key: "site_accent", purpose: "A cached copy of the site's accent color theme.", necessary: false },
  { key: "app_league", purpose: "The league you last viewed in the dashboard.", necessary: false },
  { key: "app_sidenav_pinned", purpose: "Whether you keep the sidebar pinned open.", necessary: false },
  { key: "dashboard_range", purpose: "Your chosen date range on the Dashboard.", necessary: false },
  { key: "dashboard_high_confidence_only", purpose: "The Dashboard's \"high confidence only\" filter.", necessary: false },
  { key: "market_selection", purpose: "Which markets you last chose on the Markets page.", necessary: false },
  { key: "market_picks", purpose: "Selections you've saved to \"My Picks.\"", necessary: false },
  { key: "chat_dock_open", purpose: "Whether the AI assistant panel is open or closed.", necessary: false },
  { key: "chat_fab_pos", purpose: "Where you last dragged the assistant's floating button.", necessary: false },
];

export function CookiePolicy() {
  const [branding, setBranding] = useState<Branding | null>(null);

  useEffect(() => {
    fetchBranding().then(setBranding).catch(() => setBranding(null));
  }, []);

  const whatsapp = branding?.contact_whatsapp || null;

  return (
    <PublicShell
      title="Cookie Policy — Socca Intelligence"
      description="What Socca Intelligence stores in your browser and why: no tracking or advertising cookies, just local storage for staying signed in and remembering your preferences."
    >
      <section className="doc-hero">
        <span className="eyebrow">Legal</span>
        <h1>Cookie Policy</h1>
        <p className="lede">
          The short version: this site doesn't set cookies in the traditional sense, doesn't run any
          analytics or advertising script, and doesn't track you across other sites. It does use your
          browser's local storage to keep you signed in and remember a handful of preferences -- this
          page lists every single one.
        </p>
      </section>

      <section className="doc-section">
        <h2>We don't use tracking cookies</h2>
        <p>
          Signing in works with a token stored in your browser and sent with each request, not a
          session cookie -- nothing the server sends ever tells your browser to set one. There is no
          Google Analytics, Meta Pixel, or any advertising script anywhere on this site, and we don't buy
          or sell data about visitors.
        </p>
        <p>
          We do count visits with Cloudflare Web Analytics, which sets no cookies, stores nothing in your
          browser and doesn't track you across sites or visits -- it reports page views in aggregate (which
          pages, roughly where from, what kind of device). There's accordingly nothing to opt into or out
          of beyond what's below.
        </p>
      </section>

      <section className="doc-section">
        <h2>What we store in your browser, and why</h2>
        <p>
          Everything below lives only in your own browser's local storage -- it's never transmitted
          automatically the way a cookie is, and nothing but this site reads it. <strong>Strictly
          necessary</strong> items are required for the site to function (mainly: staying signed in).{" "}
          <strong>Preference</strong> items just save you from re-choosing the same setting every visit,
          and the site works fine without them -- you'd just start from the defaults each time.
        </p>
        <div className="doc-table-wrap">
          <table className="doc-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Purpose</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {STORAGE_ITEMS.map((item) => (
                <tr key={item.key}>
                  <td><code>{item.key}</code></td>
                  <td>{item.purpose}</td>
                  <td>{item.necessary ? "Strictly necessary" : "Preference"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="doc-section">
        <h2>Links to other sites</h2>
        <p>
          Some pages link out to places we don't control -- WhatsApp, GitHub, and the Gambling Therapy and
          Gamblers Anonymous support sites in the footer. Once you follow one of those links you're on
          their site, under their own cookie and privacy policies, not ours.
        </p>
      </section>

      <section className="doc-section">
        <h2>Your choices</h2>
        <p>
          You can clear everything listed above at any time from your browser's own settings (usually
          under "Site settings" or "Clear browsing data" for this site). Clearing it signs you out and
          resets every preference to its default -- nothing is lost on our end, since none of it is
          backed up anywhere else. Blocking local storage entirely in your browser will prevent signing
          in at all, since that's how your session is kept.
        </p>
      </section>

      <section className="doc-section">
        <h2>Changes to this policy</h2>
        <p>
          If what this site stores changes, this page is updated to match -- it's generated from the
          same code that does the storing, not maintained separately from it.
        </p>
      </section>

      <section className="doc-cta">
        <h2>Questions?</h2>
        <p>Reach out and we'll answer directly.</p>
        {whatsapp && (
          <div className="landing-cta-row">
            <a
              className="btn btn-lg"
              href={whatsappLink(whatsapp, "Hi, I have a question about your Cookie Policy.")}
              target="_blank"
              rel="noreferrer noopener"
            >
              Message us on WhatsApp
            </a>
          </div>
        )}
      </section>
    </PublicShell>
  );
}
