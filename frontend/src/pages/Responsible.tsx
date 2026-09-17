import { Link } from "react-router-dom";
import { PublicShell } from "../components/PublicShell";

/**
 * Responsible-use page.
 *
 * A prediction system that people may stake money on has an obligation here
 * that goes past a footer disclaimer. This page is linked from the footer of
 * every public page, from the assistant's answer to any harm-related
 * question, and from the landing page.
 */

const HELPLINES = [
  {
    region: "United Kingdom",
    name: "BeGambleAware",
    detail: "0808 8020 133 — free, confidential, 24/7",
    href: "https://www.begambleaware.org",
  },
  {
    region: "United States",
    name: "National Problem Gambling Helpline",
    detail: "1-800-522-4700 — call or text, 24/7",
    href: "https://www.ncpgambling.org/help-treatment/",
  },
  {
    region: "International",
    name: "Gamblers Anonymous",
    detail: "Local meeting finder, in person and online",
    href: "https://www.gamblersanonymous.org",
  },
  {
    region: "Australia",
    name: "Gambling Help Online",
    detail: "1800 858 858 — free, 24/7",
    href: "https://www.gamblinghelponline.org.au",
  },
];

const WARNING_SIGNS = [
  "Staking more than you decided to, or more than you can comfortably lose",
  "Increasing stakes to recover a loss — the single most reliable path from a bad week to a serious problem",
  "Borrowing, or using money set aside for rent, food, bills or someone else's needs",
  "Hiding how much you're staking, or lying about it when asked",
  "Feeling you can't stop, or feeling anxious or irritable when you try",
  "Chasing the feeling of the win rather than the outcome of the bet",
  "Gambling to escape stress, boredom, loneliness or low mood",
];

export function Responsible() {
  return (
    <PublicShell>
      <section className="doc-hero">
        <span className="eyebrow">Responsible use</span>
        <h1>What this system is, and what it isn't</h1>
        <p className="lede">
          This platform produces probabilities. It does not produce outcomes, and no part of it will
          ever tell you a result is certain. Reading this page properly is worth more than any
          prediction on the site.
        </p>
      </section>

      <section className="doc-section">
        <h2>The honest arithmetic</h2>
        <p>
          A prediction stated at 90% is expected to be wrong about one time in ten. That is not the
          system failing — that is the system working exactly as calibrated. And those losses do not
          arrive politely spaced out one in every ten; variance means they cluster, so three in a row
          is entirely ordinary and says nothing about whether the model is broken.
        </p>
        <p>
          Football is a low-scoring, high-variance sport. A single deflection, a marginal offside call,
          one goalkeeping error decides matches that the better team dominated. Professional bookmakers,
          with paid data and far larger models, achieve roughly 53-55% accuracy on match results. Any
          claim of 80%, 90% or "guaranteed" — from this system or any other — is either measuring
          something other than what you think or leaking future information into its features.
        </p>

        <div className="doc-callout warn">
          <h4>Three things nothing here will ever be</h4>
          <ul>
            <li>
              <strong>A guarantee.</strong> There is no such thing. Anyone selling you one is lying to
              you.
            </li>
            <li>
              <strong>Financial advice.</strong> This is a statistical tool, not a recommendation to
              stake money.
            </li>
            <li>
              <strong>A way to make money reliably.</strong> Because the system deliberately never looks
              at bookmaker odds, it cannot tell you whether a price offers value. A correct prediction at
              a bad price still loses money over time.
            </li>
          </ul>
        </div>
      </section>

      <section className="doc-section">
        <h2>If you do stake money</h2>
        <ul className="doc-list">
          <li>
            <strong>Decide the amount before you look at any prediction</strong>, and treat it as the
            price of entertainment — money you have already accepted losing.
          </li>
          <li>
            <strong>Never stake money you need.</strong> Rent, food, bills, debt repayments, anything
            belonging to someone else. Nothing on this page is worth that risk.
          </li>
          <li>
            <strong>Never raise a stake to recover a loss.</strong> If you take one thing from this page,
            take this one.
          </li>
          <li>
            <strong>Set a time limit as well as a money limit.</strong> Hours disappear faster than
            balances do.
          </li>
          <li>
            <strong>Don't stake while drinking, upset, or chasing a feeling.</strong> Judgement is the
            first thing to go.
          </li>
          <li>
            <strong>Keep a real record.</strong> Not a remembered one. Memory systematically
            over-weights wins.
          </li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>Signs worth taking seriously</h2>
        <p>Any one of these is worth paying attention to. Several together is worth acting on today.</p>
        <ul className="doc-list limits">
          {WARNING_SIGNS.map((sign) => (
            <li key={sign}>{sign}</li>
          ))}
        </ul>
        <p>
          If the honest answer to "could I stop if I wanted to?" is no, that matters more than any
          model output, any winning streak, and any plan to make it back. It is also far more common
          than people assume, and the help below is free and genuinely effective.
        </p>
      </section>

      <section className="doc-section">
        <h2>Free, confidential help</h2>
        <div className="helpline-grid">
          {HELPLINES.map((h) => (
            <a
              key={h.name}
              className="helpline-card"
              href={h.href}
              target="_blank"
              rel="noreferrer noopener"
            >
              <span className="helpline-region">{h.region}</span>
              <strong>{h.name}</strong>
              <span className="helpline-detail">{h.detail}</span>
            </a>
          ))}
        </div>
        <p className="doc-note">
          Not in a region listed above? Searching for your country's name plus "gambling helpline" will
          find a local equivalent. Most are free, confidential, and staffed around the clock.
        </p>
      </section>

      <section className="doc-section">
        <h2>How this system tries to behave</h2>
        <p>Design choices made specifically so the product doesn't push in the wrong direction:</p>
        <ul className="doc-list">
          <li>
            Nothing is ever labelled a guarantee, a lock, a banker or a sure thing. Outputs read "Model
            Probability: X%", always.
          </li>
          <li>
            Every prediction carries a confidence band, a data-quality score and a model-agreement score
            beside it, so a weak call looks weak instead of looking authoritative.
          </li>
          <li>
            The AI assistant answers any question about guaranteed wins or recovering losses by saying
            plainly that neither exists, and pointing here — rather than producing a prediction.
          </li>
          <li>
            The system states its own limitations on the{" "}
            <Link to="/how-it-works">methodology page</Link>, including the things it cannot see at all.
          </li>
          <li>
            No affiliate links, no bookmaker integrations, no odds comparison, nothing that earns from
            your staking.
          </li>
          <li>
            The mascot is an analyst reading data — never holding a trophy, dice, or cash.
          </li>
        </ul>
      </section>

      <section className="doc-cta">
        <h2>18+. Predictions are probabilities, never guarantees.</h2>
        <div className="landing-cta-row">
          <Link className="btn ghost btn-lg" to="/how-it-works">
            Read the methodology
          </Link>
          <Link className="btn ghost btn-lg" to="/">
            Back to home
          </Link>
        </div>
      </section>
    </PublicShell>
  );
}
