import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchBranding } from "../api";
import { PublicShell } from "../components/PublicShell";
import { whatsappLink } from "../lib/whatsapp";
import type { Branding } from "../types";

/**
 * Public pricing page.
 *
 * There is still no in-app payment gateway -- see Welcome.tsx and
 * Register.tsx, which say the same thing. This exists so pricing has its
 * own linkable, indexable page instead of being buried inside Welcome's
 * "Getting access" section, which still carries its own shorter version of
 * the same price callout for anyone who never scrolls this far.
 */

export function Pricing() {
  const [branding, setBranding] = useState<Branding | null>(null);

  useEffect(() => {
    fetchBranding().then(setBranding).catch(() => setBranding(null));
  }, []);

  // "" (not set) falls back to the generic "arrange payment externally"
  // wording every price mention on this site already has -- same
  // convention as Welcome.tsx/Access.tsx/Register.tsx.
  const price = branding?.pricing_label || null;
  const whatsapp = branding?.contact_whatsapp || null;

  return (
    <PublicShell
      title="Pricing — Football & Soccer Predictions | Socca Intelligence"
      description="What access to Socca Intelligence's football and soccer predictions costs: no in-app payment gateway, a Super Admin issues a time-limited access code once payment is arranged outside the platform."
    >
      <section className="doc-hero">
        <span className="eyebrow">Pricing</span>
        <h1>{price ? `Access is ${price}` : "How pricing works"}</h1>
        <p className="lede">
          No payment form lives on this platform -- that happens between you and a Super Admin, outside
          the platform. What you get once a code is redeemed is the same either way: the full product,
          nothing held back for a higher tier.
        </p>
      </section>

      {price && (
        <section className="doc-section" style={{ textAlign: "center" }}>
          <div className="accuracy-panel" style={{ maxWidth: 420, margin: "0 auto" }}>
            <span className="accuracy-number">{price}</span>
            <span className="accuracy-caption">
              Full access -- every Guda Pick, Admin Pick, and the weekly and random accumulators, across
              every match we cover, for the code's duration.
            </span>
          </div>
        </section>
      )}

      <section className="doc-section">
        <h2>What's included</h2>
        <p>
          One price, everything unlocked -- there is no free tier that holds back markets or cuts off
          after a few picks. A redeemed code gets you:
        </p>
        <ul className="doc-list">
          <li>Per-match analysis across every league this deployment covers, not a subset.</li>
          <li>
            Every Guda Pick and Admin Pick our team curates, plus the system's own weekly and random
            accumulators.
          </li>
          <li>Live, in-play updates for matches currently being played.</li>
          <li>The AI assistant, for asking about any match, team or market directly.</li>
          <li>AI Generation -- build your own combo from any markets you choose, priced from real odds.</li>
        </ul>
        <p>
          Access expires automatically at the end of the code's stated duration. Nothing renews or
          charges itself -- a new code is the only way to extend it, same as the first one.
        </p>
      </section>

      <section className="doc-section">
        <h2>How to get a code</h2>
        <div className="access-steps">
          <div className="access-step">
            <span className="access-step-num">1</span>
            <h3>Arrange payment externally</h3>
            <p>
              Pay through whatever business channel the administrator uses -- mobile money, bank
              transfer, cash. Nothing here processes a card or takes a payment.
              {price && <> Current price: <strong>{price}</strong>.</>}
            </p>
          </div>
          <div className="access-step">
            <span className="access-step-num">2</span>
            <h3>Receive an access code</h3>
            <p>
              Once payment is confirmed, the Super Admin generates a unique, time-limited code and sends
              it to you. Register and sign in any time before or after -- the account itself is free and
              instant.
            </p>
          </div>
          <div className="access-step">
            <span className="access-step-num">3</span>
            <h3>Redeem it</h3>
            <p>
              Enter the code on the Access page. It activates immediately for its stated duration --
              dashboard, per-match analysis, live in-play updates, team pages and the AI assistant, all
              unlocked.
            </p>
          </div>
        </div>
      </section>

      <section className="doc-cta">
        <h2>Ready to get access?</h2>
        <p>Create an account first -- it's free and instant -- then arrange a code whenever you're ready.</p>
        <div className="landing-cta-row">
          <Link className="btn btn-lg" to="/register">
            Create an account
          </Link>
          {whatsapp && (
            <a
              className="btn ghost btn-lg"
              href={whatsappLink(whatsapp, "Hi, I'd like an access code for Socca Intelligence.")}
              target="_blank"
              rel="noreferrer noopener"
            >
              Message us on WhatsApp
            </a>
          )}
        </div>
      </section>
    </PublicShell>
  );
}
