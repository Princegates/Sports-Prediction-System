import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchBranding } from "../api";
import { PublicShell } from "../components/PublicShell";
import { usePageStructuredData } from "../lib/pageMeta";
import { whatsappLink } from "../lib/whatsapp";
import type { Branding } from "../types";

/**
 * Public pricing page.
 *
 * Leads with the three ways in (free, the trial, full access) and a trial
 * button on the first screen. There's still no in-app payment gateway:
 * payment happens outside the platform and our team sends an access code.
 *
 * The headline is static text on purpose -- it used to read "Access is
 * <price>", which changed once branding loaded and shifted the whole page
 * (layout shift 0.24 on phones). The price now loads into fixed-size tiles.
 */

const INCLUDED = [
  "A probability and confidence rating for every market on every match we cover",
  "Every pick our team publishes, plus the weekly and random accumulators",
  "AI Generation: build a slip from the markets and leagues you choose",
  "Live, in-play updates while matches are being played",
  "The AI assistant, for questions about any match, team or market",
];

export function Pricing() {
  const [branding, setBranding] = useState<Branding | null>(null);

  useEffect(() => {
    fetchBranding().then(setBranding).catch(() => setBranding(null));
  }, []);

  const price = branding?.pricing_label || null;
  const perDay = branding?.price_per_day || null;
  const trialDays = branding?.trial_days ?? 0;
  const whatsapp = branding?.contact_whatsapp || null;
  const trialCta = trialDays > 0 ? `Start your ${trialDays}-day free trial` : "Create a free account";

  const faqs = [
    {
      q: "How do I pay?",
      a: "Message our team on WhatsApp and pay by mobile money or bank transfer. Nothing on this site takes card details or processes a payment.",
    },
    {
      q: "How quickly do I get my code?",
      a: "As soon as the payment is confirmed. From the Access page you can send the team your payment reference, which tells them straight away, and the code arrives by email or WhatsApp.",
    },
    {
      q: "What happens when my access ends?",
      a: "You drop back to the free tier. Nothing renews or charges by itself, and your account, history and saved slips all stay. You get an email a day before it ends.",
    },
    {
      q: "Do I need an account first?",
      a: trialDays > 0
        ? `It's best to create one first: it's free, and it starts your ${trialDays}-day trial. You can redeem a code any time after that.`
        : "Yes, a free account. Create it first, then redeem your code on the Access page.",
    },
  ];

  usePageStructuredData({
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((f) => ({ "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a } })),
  });

  return (
    <PublicShell
      title="Pricing — Football & Soccer Predictions | Socca Intelligence"
      description="Start free with a trial -- no card, no code. Full access to every football and soccer prediction is a single time-limited code, paid by mobile money or bank transfer."
    >
      <section className="doc-hero">
        <span className="eyebrow">Pricing</span>
        <h1>Start free. Pay when it's worth it.</h1>
        <p className="lede">
          {trialDays > 0
            ? `Every new account gets ${trialDays} days of full access with no card and no code. After that, one simple price unlocks everything.`
            : "Creating an account is free. One simple price unlocks everything."}
        </p>
        <div className="landing-cta-row">
          <Link className="btn btn-lg" to="/register">
            {trialCta}
          </Link>
          {whatsapp && (
            <a
              className="btn ghost btn-lg"
              href={whatsappLink(whatsapp, `Hi, I'd like an access code for Socca Intelligence.${price ? ` (${price})` : ""}`)}
              target="_blank"
              rel="noreferrer noopener"
            >
              Buy access on WhatsApp
            </a>
          )}
        </div>
      </section>

      <section className="doc-section">
        <div className="plan-strip">
          <div className="plan-tile">
            <span className="plan-name">Free</span>
            <span className="plan-price">Free</span>
            <span className="plan-desc">One headline pick per league, plus the public pick of the day.</span>
          </div>
          {trialDays > 0 && (
            <div className="plan-tile featured">
              <span className="plan-name">Free trial</span>
              <span className="plan-price">
                {trialDays} day{trialDays === 1 ? "" : "s"}
              </span>
              <span className="plan-desc">Everything in full access. Starts when you confirm your email.</span>
            </div>
          )}
          <div className="plan-tile">
            <span className="plan-name">Full access</span>
            <span className="plan-price">{branding === null ? "…" : price ?? "Ask us"}</span>
            <span className="plan-desc">
              {perDay ? `${perDay[0].toUpperCase()}${perDay.slice(1)}. ` : ""}Everything, for the code's duration. Nothing
              auto-renews.
            </span>
          </div>
        </div>
      </section>

      <section className="doc-section">
        <h2>What full access includes</h2>
        <ul className="doc-list">
          {INCLUDED.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="doc-section">
        <h2>How paying works</h2>
        <div className="access-steps">
          <div className="access-step">
            <span className="access-step-num">1</span>
            <h3>Pay our team</h3>
            <p>
              Message us on WhatsApp and pay by mobile money or bank transfer.
              {price && (
                <>
                  {" "}
                  Current price: <strong>{price}</strong>
                  {perDay && <> ({perDay})</>}.
                </>
              )}
            </p>
          </div>
          <div className="access-step">
            <span className="access-step-num">2</span>
            <h3>Get your code</h3>
            <p>Once the payment is confirmed you get a personal access code by email or WhatsApp.</p>
          </div>
          <div className="access-step">
            <span className="access-step-num">3</span>
            <h3>Redeem it</h3>
            <p>Enter it on the Access page. Everything unlocks straight away, for the code's full duration.</p>
          </div>
        </div>
      </section>

      <section className="doc-section">
        <h2>Questions</h2>
        <div className="faq-list">
          {faqs.map((f) => (
            <div key={f.q} className="faq-item open">
              <p className="faq-question" style={{ cursor: "default" }}>
                <span>{f.q}</span>
              </p>
              <p className="faq-answer">{f.a}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="doc-cta">
        <h2>Try it before you pay</h2>
        <p>{trialDays > 0 ? `${trialDays} days of everything, no card, no code.` : "Create a free account in about 30 seconds."}</p>
        <div className="landing-cta-row">
          <Link className="btn btn-lg" to="/register">
            {trialCta}
          </Link>
        </div>
      </section>
    </PublicShell>
  );
}
