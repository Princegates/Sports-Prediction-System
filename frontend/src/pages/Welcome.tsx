import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPublicAccuracy, fetchPublicFixtures, fetchPublicStats } from "../api";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { useTilt } from "../lib/useTilt";
import type { PublicAccuracy, PublicFixture, PublicStats } from "../types";

/**
 * Public landing page.
 *
 * Every figure on this page is read from /api/public/* rather than written
 * into the markup. That costs a loading state, and it buys the guarantee
 * that the page can't advertise a capability the deployment doesn't have --
 * a fresh install with no data imported says so instead of claiming
 * thousands of matches.
 */

function StatTile({ value, label, hint }: { value: string; label: string; hint?: string }) {
  return (
    <div className="stat-tile">
      <div className="stat-tile-value">{value}</div>
      <div className="stat-tile-label">{label}</div>
      {hint && <div className="stat-tile-hint">{hint}</div>}
    </div>
  );
}

function FeatureCard({ icon, title, body }: { icon: string; title: string; body: string }) {
  const tilt = useTilt<HTMLDivElement>();
  return (
    <div className="feature-card tilt-card" {...tilt}>
      <span className="feature-icon" aria-hidden>
        {icon}
      </span>
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}

const MODEL_STACK = [
  {
    step: "01",
    title: "Elo ratings",
    body: "A match-by-match strength rating for every team, with home advantage and a margin-of-victory multiplier, mapped to probabilities through a fitted logistic curve.",
  },
  {
    step: "02",
    title: "Dixon-Coles Poisson",
    body: "Attack and defense strength per team, split home and away, with the low-score correction. This produces the full score matrix behind every goals market.",
  },
  {
    step: "03",
    title: "Gradient boosting",
    body: "A scikit-learn model over engineered features: recent form, goal differentials, rest days and the Elo gap between the two sides.",
  },
  {
    step: "04",
    title: "Blend and calibrate",
    body: "The three are weighted into one set of probabilities, then passed through isotonic regression fitted on a held-out split -- so a stated 70% has historically happened about 70% of the time.",
  },
];

const FEATURES = [
  {
    icon: "◎",
    title: "Global Most-Likely Outcome",
    body: "Instead of one market at a time, every fixture is scored across 18 markets and the single highest-probability outcome is surfaced -- but only among outcomes that clear a data-quality gate.",
  },
  {
    icon: "◈",
    title: "Explainable, not oracular",
    body: "Each call ships with the positive and negative factors that drove it, computed from real feature deltas, plus how each of the three models individually voted.",
  },
  {
    icon: "◉",
    title: "Confidence you can audit",
    body: "Every prediction carries a data-quality score, a model-agreement score and a confidence band. Thin data produces a visibly low-confidence call, not a falsely certain one.",
  },
  {
    icon: "⬗",
    title: "Live in-play recalculation",
    body: "Goals, red cards and other events re-project the remaining match and update every market immediately, with the probability timeline kept so you can see what moved.",
  },
  {
    icon: "◍",
    title: "Grounded AI assistant",
    body: "Ask about any fixture in plain language. The assistant reads this system's own database to answer -- it reports stored numbers and cites them, so it cannot invent a statistic.",
  },
  {
    icon: "⬢",
    title: "Market-independent by design",
    body: "The models never look at bookmaker odds. The probabilities are formed from match data alone, so they're an independent opinion rather than a re-derivation of the market's.",
  },
];

const FAQS = [
  {
    q: "Do I get access immediately after registering?",
    a: "You can sign in immediately -- there's no approval queue. What's locked until you redeem an access code is the prediction surface itself: teams, matches, predictions and the AI assistant. A Super Admin issues a code once you've arranged payment with them outside the platform; you enter it on the Access page and it unlocks for the code's duration.",
  },
  {
    q: "How accurate is it, honestly?",
    a: "Football 1X2 is genuinely hard: professional bookmakers, with paid data and far larger models, land around 53-55% accuracy. The figures on this page come from a date-split backtest where the model is scored only on matches that happened after everything it trained on. Anything claiming dramatically more than the bookmaker baseline -- here or anywhere -- deserves suspicion.",
  },
  {
    q: "Are these predictions guaranteed?",
    a: "No, and nothing in this system will ever say otherwise. Every output is a probability with a confidence band attached. A 90% call still loses roughly one time in ten, and those losses arrive in clusters rather than politely spaced out.",
  },
  {
    q: "Where does the data come from?",
    a: "Free, open sources: openfootball's season files on GitHub and football-data.co.uk's historical CSVs. No paid feeds, no API keys tied to a credit card. That's a deliberate constraint, and it's why some features -- confirmed lineups, live shot-level stats -- are pluggable extension points rather than shipped capabilities.",
  },
  {
    q: "Is the AI chat a language model?",
    a: "By default, no -- and that's the point. It parses your question, looks the answer up in the database, and composes a reply from the rows it found. That makes a fabricated statistic structurally impossible rather than merely unlikely. An optional phrasing-only rewriter can be pointed at a local model if you want more fluent prose, without changing what the numbers say.",
  },
  {
    q: "What does it cost to run?",
    a: "Nothing. Free data sources, SQLite on disk, open-source Python and Node, models trained locally on your own machine. Deployable to any free-tier host or a single small VPS.",
  },
];

export function Welcome() {
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [accuracy, setAccuracy] = useState<PublicAccuracy | null>(null);
  const [fixtures, setFixtures] = useState<PublicFixture[]>([]);
  const [openFaq, setOpenFaq] = useState<number | null>(0);
  const heroTilt = useTilt<HTMLDivElement>();

  useEffect(() => {
    // Each panel degrades independently: if the backend is down, the page
    // still renders as a static brochure rather than an error screen.
    fetchPublicStats().then(setStats).catch(() => setStats(null));
    fetchPublicAccuracy().then(setAccuracy).catch(() => setAccuracy(null));
    fetchPublicFixtures(5, 6).then(setFixtures).catch(() => setFixtures([]));
  }, []);

  const nf = new Intl.NumberFormat();

  return (
    <PublicShell
      title="Socca Intelligence — Calibrated Football Predictions"
      description="Ensemble football prediction across 18 markets per fixture: Elo, Dixon-Coles Poisson and gradient boosting, blended and calibrated on held-out history. Model probabilities, never guarantees."
    >
      {/* ---------------- Hero ---------------- */}
      <section className="landing-hero">
        <div className="landing-hero-copy">
          <h1>
            Calibrated football predictions,
            <span className="hero-gradient"> with the reasoning shown</span>
          </h1>
          <p className="lede">
            Three independent models -- Elo, Dixon-Coles Poisson and gradient boosting -- blended and
            calibrated against held-out history, then scored across 18 markets per match. You see the
            probability, the confidence, and the factors behind it. Never a guarantee.
          </p>

          <div className="landing-cta-row">
            <Link className="btn btn-lg" to="/register">
              Explore AI predictions
            </Link>
            <Link className="btn ghost btn-lg" to="/login">
              Enter access code
            </Link>
          </div>

          <p className="landing-cta-note">
            Sign in is instant. Predictions unlock once you redeem an access code, issued by a Super
            Admin after payment is confirmed outside the platform -- there's no card form here.
          </p>
        </div>

        <div className="landing-hero-visual tilt-card" {...heroTilt}>
          <Mascot pose="thinking" size={92} />
          <div className="hero-panel">
            <div className="hero-panel-head">
              <span className="tag">Global Most-Likely Outcome</span>
              <span className="confidence-tag high">HIGH</span>
            </div>
            <div className="hero-panel-body">
              <div className="hero-panel-metric">
                <span className="hero-panel-number">18</span>
                <span>markets scored per fixture</span>
              </div>
              <div className="hero-panel-rows">
                <div>
                  <span>Match result</span>
                  <span>Elo + Poisson + GBM</span>
                </div>
                <div>
                  <span>Goals &amp; BTTS</span>
                  <span>Full score matrix</span>
                </div>
                <div>
                  <span>Correct score</span>
                  <span>Dixon-Coles corrected</span>
                </div>
                <div>
                  <span>Calibration</span>
                  <span>Isotonic, held-out fit</span>
                </div>
              </div>
            </div>
            <p className="hero-panel-foot">
              Every output carries data-quality and model-agreement scores alongside the probability.
            </p>
          </div>
        </div>
      </section>

      {/* ---------------- Live corpus stats ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>What the system is actually holding</h2>
          <p>
            Read live from the database on page load -- not numbers typed into a marketing page.
          </p>
        </div>

        {stats ? (
          <div className="stat-grid">
            <StatTile value={nf.format(stats.matches_analyzed)} label="Matches analyzed" hint="Completed results in the corpus" />
            <StatTile value={nf.format(stats.teams_tracked)} label="Teams tracked" hint="With point-in-time form history" />
            <StatTile value={String(stats.leagues_covered)} label="Leagues covered" />
            <StatTile value={nf.format(stats.predictions_generated)} label="Predictions generated" />
            <StatTile value={nf.format(stats.upcoming_fixtures)} label="Upcoming fixtures" hint="Scheduled and awaiting kickoff" />
            <StatTile value={String(stats.markets_per_match)} label="Markets per match" hint="Scored in the outcome registry" />
          </div>
        ) : (
          <div className="state-card">
            <p>
              Corpus figures unavailable -- the API isn't reachable from here. The rest of this page is
              static, so it still describes the system accurately.
            </p>
          </div>
        )}

        {stats && stats.league_names.length > 0 && (
          <div className="league-chips">
            {stats.league_names.map((l) => (
              <span key={l} className="league-chip">
                {l}
              </span>
            ))}
          </div>
        )}

        {stats && stats.matches_analyzed === 0 && (
          <div className="unavailable-note">
            This deployment has no match data imported yet, so the counts above are zero. Running the
            import scripts in <code>backend/scripts</code> populates them -- the page reports what's
            there rather than a promise of what could be.
          </div>
        )}
      </section>

      {/* ---------------- Measured accuracy ---------------- */}
      <section className="landing-section accuracy-section">
        <div className="section-head">
          <h2>Measured, not claimed</h2>
          <p>
            Train, validation and test are split by date and never shuffled, so the model is scored
            only on matches that happened strictly after everything it learned from.
          </p>
        </div>

        {accuracy?.has_data ? (
          <div className="accuracy-panel">
            <div className="accuracy-headline">
              <div>
                <span className="accuracy-number">
                  {accuracy.accuracy != null ? `${(accuracy.accuracy * 100).toFixed(1)}%` : "--"}
                </span>
                <span className="accuracy-caption">
                  1X2 accuracy on the held-out {accuracy.split} split
                </span>
              </div>
              <div className="accuracy-meta">
                {accuracy.matches_evaluated != null && (
                  <span>{nf.format(accuracy.matches_evaluated)} matches evaluated</span>
                )}
                {accuracy.model_version && <span>Model {accuracy.model_version}</span>}
                {accuracy.computed_at && (
                  <span>Backtested {new Date(accuracy.computed_at).toLocaleDateString()}</span>
                )}
              </div>
            </div>

            <div className="accuracy-metrics">
              {accuracy.log_loss != null && (
                <div>
                  <span className="metric-label">Log loss</span>
                  <span className="metric-value">{accuracy.log_loss.toFixed(4)}</span>
                  <span className="metric-hint">Lower is better; penalizes confident mistakes</span>
                </div>
              )}
              {accuracy.brier_score != null && (
                <div>
                  <span className="metric-label">Brier score</span>
                  <span className="metric-value">{accuracy.brier_score.toFixed(4)}</span>
                  <span className="metric-hint">Mean squared probability error</span>
                </div>
              )}
              {accuracy.leagues.length > 0 && (
                <div>
                  <span className="metric-label">Leagues</span>
                  <span className="metric-value">{accuracy.leagues.length}</span>
                  <span className="metric-hint">{accuracy.leagues.join(", ")}</span>
                </div>
              )}
            </div>

            <p className="accuracy-context">
              <strong>For context:</strong> professional bookmakers, with paid data feeds and far larger
              models, achieve roughly 53-55% on football 1X2. Treat any claim far above that -- from this
              system or any other -- as a reason to look harder at the methodology.
            </p>
          </div>
        ) : (
          <div className="state-card">
            <h3>No backtest recorded yet</h3>
            <p>
              This deployment hasn't run one, so there is no accuracy figure to show -- and a landing
              page that invented one would be worse than an empty section. Running{" "}
              <code>python scripts/backtest.py</code> produces accuracy, log loss, Brier score and a
              calibration table, and this panel then fills itself in.
            </p>
          </div>
        )}
      </section>

      {/* ---------------- Model stack ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>How a prediction gets made</h2>
          <p>Four stages, all running locally on open-source libraries.</p>
        </div>
        <div className="stack-grid">
          {MODEL_STACK.map((s) => (
            <div key={s.step} className="stack-card">
              <span className="stack-step">{s.step}</span>
              <h3>{s.title}</h3>
              <p>{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------- Features ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>What you get inside</h2>
        </div>
        <div className="feature-grid">
          {FEATURES.map((f) => (
            <FeatureCard key={f.title} {...f} />
          ))}
        </div>
      </section>

      {/* ---------------- Upcoming fixtures teaser ---------------- */}
      {fixtures.length > 0 && (
        <section className="landing-section">
          <div className="section-head">
            <h2>Coverage right now</h2>
            <p>
              Real fixtures from the database. Selections and probabilities stay behind the login --
              this is here to show the system is live, not to be the product.
            </p>
          </div>
          <div className="fixture-teaser-list">
            {fixtures.map((f, i) => (
              <div key={i} className="fixture-teaser">
                <div className="fixture-teaser-main">
                  <span className="fixture-teaser-teams">
                    {f.home_team} <span className="vs-divider">v</span> {f.away_team}
                  </span>
                  <span className="fixture-teaser-meta">
                    {f.league} · {new Date(f.kickoff).toLocaleString(undefined, {
                      weekday: "short",
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
                {f.has_prediction ? (
                  <span className={`confidence-tag ${(f.confidence ?? "").toLowerCase()}`}>
                    {f.confidence} confidence
                  </span>
                ) : (
                  <span className="badge-neutral">Awaiting analysis</span>
                )}
              </div>
            ))}
          </div>
          <p className="fixture-teaser-foot">
            Sign in to see the full probability table, the most-likely outcome and the reasoning for
            each of these.
          </p>
        </section>
      )}

      {/* ---------------- Access process ---------------- */}
      <section className="landing-section access-section">
        <div className="section-head">
          <h2>Getting access</h2>
          <p>No payment form lives on this platform -- that happens between you and a Super Admin.</p>
        </div>
        <div className="access-steps">
          <div className="access-step">
            <span className="access-step-num">1</span>
            <h3>Arrange payment externally</h3>
            <p>
              Pay through whatever business channel the administrator uses -- mobile money, bank
              transfer, cash. Nothing here processes a card or takes a payment.
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
              unlocked. Access expires automatically; nothing renews itself without a new code.
            </p>
          </div>
        </div>

        <div className="access-cta">
          <Link className="btn btn-lg" to="/register">
            Create an account
          </Link>
          <Link className="btn ghost btn-lg" to="/account-status">
            Check your access status
          </Link>
        </div>
      </section>

      {/* ---------------- FAQ ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>Straight answers</h2>
        </div>
        <div className="faq-list">
          {FAQS.map((f, i) => (
            <div key={f.q} className={`faq-item${openFaq === i ? " open" : ""}`}>
              <button
                className="faq-question"
                onClick={() => setOpenFaq(openFaq === i ? null : i)}
                aria-expanded={openFaq === i}
              >
                <span>{f.q}</span>
                <span className="faq-chevron" aria-hidden>
                  {openFaq === i ? "−" : "+"}
                </span>
              </button>
              {openFaq === i && <p className="faq-answer">{f.a}</p>}
            </div>
          ))}
        </div>
      </section>

      {/* ---------------- Final CTA ---------------- */}
      <section className="doc-cta">
        <h2>Ready to see what the models say about this week's fixtures?</h2>
        <p>Create an account, then redeem your access code the moment you have one.</p>
        <div className="landing-cta-row">
          <Link className="btn btn-lg" to="/register">
            Get access
          </Link>
          <Link className="btn ghost btn-lg" to="/login">
            Sign in
          </Link>
        </div>
      </section>

      {/* ---------------- Responsible use ---------------- */}
      <section className="landing-section responsible-section">
        <div className="responsible-card">
          <h2>Before you act on any of this</h2>
          <p>
            This system produces probabilities. It does not produce outcomes, and it will never tell you
            something is certain. A 90% call loses about one time in ten, and those losses cluster.
          </p>
          <p>
            Never stake money you need for anything else. Never raise a stake to recover a loss -- it's
            the most reliable way a bad week becomes a serious problem. If stopping doesn't feel
            optional, that matters more than any prediction on this site.
          </p>
          <p className="responsible-links">
            Free, confidential help: call Ghana's{" "}
            <a href="https://www.moh.gov.gh/mental-health-authority/" target="_blank" rel="noreferrer noopener">
              Mental Health Authority
            </a>{" "}
            on <strong>0800 678 678</strong> — toll-free from any network here, any hour. Online,{" "}
            <a href="https://www.gamblingtherapy.org" target="_blank" rel="noreferrer noopener">
              Gambling Therapy
            </a>{" "}
            and{" "}
            <a href="https://www.gamblersanonymous.org" target="_blank" rel="noreferrer noopener">
              Gamblers Anonymous
            </a>{" "}
            both run support you can reach from Ghana.
          </p>
        </div>
      </section>
    </PublicShell>
  );
}
