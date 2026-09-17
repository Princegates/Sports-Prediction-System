import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPublicAccuracy, fetchPublicStats } from "../api";
import { PublicShell } from "../components/PublicShell";
import type { PublicAccuracy, PublicStats } from "../types";

/**
 * Public methodology page.
 *
 * This exists because "trust us, it's AI" is not a claim anyone should
 * accept. Everything here is checkable against the source: the models named
 * are the modules that run, the limitations listed are real, and the
 * accuracy figures are read from whatever backtest this deployment actually
 * performed rather than asserted.
 */

export function HowItWorks() {
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [accuracy, setAccuracy] = useState<PublicAccuracy | null>(null);

  useEffect(() => {
    fetchPublicStats().then(setStats).catch(() => setStats(null));
    fetchPublicAccuracy().then(setAccuracy).catch(() => setAccuracy(null));
  }, []);

  const nf = new Intl.NumberFormat();

  return (
    <PublicShell>
      <section className="doc-hero">
        <span className="eyebrow">Methodology</span>
        <h1>How the predictions are made</h1>
        <p className="lede">
          No black box. Three named statistical models, a documented blending step, a calibration pass
          fitted on held-out data, and an outcome selector with an explicit data-quality gate. Here is
          each stage, including where it falls short.
        </p>
      </section>

      {/* --- The models --- */}
      <section className="doc-section">
        <h2>1. Three independent models</h2>
        <p>
          Each model sees the same match data and forms its own opinion. They fail in different ways,
          which is the entire reason for running three rather than tuning one.
        </p>

        <div className="doc-model-grid">
          <div className="doc-model">
            <h3>Elo ratings</h3>
            <p>
              A single strength number per team, updated after every match by how surprising the result
              was and by the margin of victory. Home advantage enters as a fixed rating offset. The
              rating difference is mapped to home/draw/away probabilities through a logistic curve
              fitted on historical outcomes.
            </p>
            <p className="doc-model-limit">
              <strong>Weakness:</strong> knows nothing about goals, only about who beat whom. It cannot
              tell a 1-0 grinder from a 4-3 thriller when both teams end up equally rated.
            </p>
          </div>

          <div className="doc-model">
            <h3>Dixon-Coles Poisson</h3>
            <p>
              Models goals directly: an attack strength and a defense strength per team, estimated
              separately for home and away, producing an expected goal rate for each side. Those two
              rates generate the full probability matrix over every scoreline, which is where over/under
              lines, both-teams-to-score and correct-score numbers all come from. The Dixon-Coles
              correction fixes a known flaw in plain Poisson, which understates 0-0, 1-0, 0-1 and 1-1.
            </p>
            <p className="doc-model-limit">
              <strong>Weakness:</strong> assumes the two teams' goal counts are independent, which they
              are not -- a team leading 3-0 stops attacking.
            </p>
          </div>

          <div className="doc-model">
            <h3>Gradient boosting</h3>
            <p>
              A scikit-learn ensemble over engineered features: points per game across a recent window,
              goals scored and conceded, home/away splits, clean-sheet rate, days of rest, and the Elo
              gap. It finds interactions the first two models can't express by construction.
            </p>
            <p className="doc-model-limit">
              <strong>Weakness:</strong> it needs training data per league. Where a league hasn't been
              trained, it drops out of the blend entirely and the other two carry the prediction -- the
              match page tells you when that happened.
            </p>
          </div>
        </div>
      </section>

      {/* --- Blending --- */}
      <section className="doc-section">
        <h2>2. Blending and calibration</h2>
        <p>
          The three sets of probabilities are combined on configured weights, then renormalized. That
          gives a sharper prediction than any single model, but a blended probability is not
          automatically an <em>honest</em> one -- a model can be 70% confident on a set of matches that
          only happen 55% of the time.
        </p>
        <p>
          So the blend goes through <strong>isotonic regression</strong> fitted on a validation split
          the models never trained on. This is the step that makes the numbers mean what they say: after
          calibration, the matches where the system says 70% are matches that historically resolved that
          way about 70% of the time.
        </p>
        <div className="doc-callout">
          <h4>Why calibration matters more than accuracy</h4>
          <p>
            A model that says "55%" on every match can be well calibrated and nearly useless. A model
            that says "95%" on everything can be accurate on the easy majority and catastrophically
            wrong when it matters. Accuracy alone hides both failures, which is why every prediction here
            also reports its confidence band, data-quality score and model agreement.
          </p>
        </div>
      </section>

      {/* --- Outcome engine --- */}
      <section className="doc-section">
        <h2>3. The Global Most-Likely Outcome</h2>
        <p>
          Rather than showing one market and leaving you to compare, every fixture is scored across the
          full outcome registry -- match result, double chance, both-teams-to-score, five over/under
          lines, and the correct-score grid -- and the single highest-probability outcome is surfaced.
        </p>
        <p>
          The registry records which outcomes are mutually exclusive (home/draw/away must sum to one)
          and which are not (a home win and over 2.5 goals can both happen), so downstream consumers
          can reason correctly about combinations. Crucially, each outcome also carries a{" "}
          <strong>minimum data requirement</strong>: outcomes whose gate a fixture fails are excluded
          before the comparison happens. A team with three matches of history cannot produce a
          confident-looking correct-score call.
        </p>
        <div className="doc-callout warn">
          <h4>High probability is not high value</h4>
          <p>
            The most-likely outcome is frequently a low-information one -- a heavy favourite, or an
            "over 0.5 goals" line that clears 95%. This system deliberately never looks at bookmaker
            odds, which means it forms an independent opinion but also that it{" "}
            <strong>cannot tell you whether a price is good</strong>. If you want value rather than
            likelihood, that comparison is yours to make.
          </p>
        </div>
      </section>

      {/* --- Backtesting --- */}
      <section className="doc-section">
        <h2>4. How it's evaluated</h2>
        <p>
          Train, validation and test are split <strong>by date and never shuffled</strong>. Every
          feature is computed as-of the fixture's own kickoff, reading only matches strictly before it.
          That's what makes the evaluation meaningful: a shuffled split would let the model learn from
          March to predict February and report a wonderful, fictional accuracy.
        </p>

        {accuracy?.has_data ? (
          <div className="doc-metrics">
            <div>
              <span className="metric-label">1X2 accuracy ({accuracy.split} split)</span>
              <span className="metric-value">
                {accuracy.accuracy != null ? `${(accuracy.accuracy * 100).toFixed(1)}%` : "--"}
              </span>
            </div>
            {accuracy.log_loss != null && (
              <div>
                <span className="metric-label">Log loss</span>
                <span className="metric-value">{accuracy.log_loss.toFixed(4)}</span>
              </div>
            )}
            {accuracy.brier_score != null && (
              <div>
                <span className="metric-label">Brier score</span>
                <span className="metric-value">{accuracy.brier_score.toFixed(4)}</span>
              </div>
            )}
            {accuracy.matches_evaluated != null && (
              <div>
                <span className="metric-label">Matches evaluated</span>
                <span className="metric-value">{nf.format(accuracy.matches_evaluated)}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="unavailable-note">
            This deployment has no recorded backtest, so there are no figures to show here. The page
            reports that rather than displaying a placeholder number.
          </div>
        )}

        <p>
          <strong>The baseline that matters:</strong> professional bookmakers, with paid feeds and
          larger models, land around 53-55% on football 1X2. Football is a low-scoring sport with
          enormous variance; a single deflection decides matches. Any system claiming 80%+ on match
          results is either measuring something other than what you think, or leaking future
          information into its features.
        </p>
      </section>

      {/* --- Data --- */}
      <section className="doc-section">
        <h2>5. Where the data comes from</h2>
        <p>Free and open sources only -- no paid feeds and no keys tied to a credit card.</p>
        <ul className="doc-list">
          <li>
            <strong>openfootball/football.json</strong> — season files on GitHub carrying both completed
            results and genuine not-yet-played fixtures. No signup.
          </li>
          <li>
            <strong>football-data.co.uk</strong> — historical results CSVs going back many seasons
            across 20+ leagues. No signup.
          </li>
          <li>
            <strong>SQLite</strong> on disk for storage, swappable for Postgres by changing one
            environment variable.
          </li>
          <li>
            <strong>scikit-learn, numpy, pandas</strong> — all training runs locally on your own
            hardware.
          </li>
        </ul>
        {stats && (
          <p>
            Right now this deployment holds {nf.format(stats.matches_analyzed)} completed matches across{" "}
            {stats.leagues_covered} league{stats.leagues_covered === 1 ? "" : "s"}, covering{" "}
            {nf.format(stats.teams_tracked)} teams.
          </p>
        )}
      </section>

      {/* --- Limitations --- */}
      <section className="doc-section">
        <h2>6. What it can't do</h2>
        <p>
          Listing these is not modesty; it's the difference between a tool you can calibrate your trust
          against and one you can't.
        </p>
        <ul className="doc-list limits">
          <li>
            <strong>No confirmed lineups or injury data.</strong> There's no free source of adequate
            quality. A key striker being out is invisible to the model until it shows up in results.
            The recalculation logic is built and waiting for a provider to be plugged in.
          </li>
          <li>
            <strong>No live shot-level statistics.</strong> In-play updates work by time-scaling the
            pre-match expected goals against the current score, adjusted for red cards. That's an
            honest approximation, not a substitute for live xG.
          </li>
          <li>
            <strong>No referee, weather or travel modelling.</strong> Each is a documented extension
            point rather than a shipped feature.
          </li>
          <li>
            <strong>No head-to-head weighting.</strong> Deliberate: squads turn over enough that a
            result from three seasons ago carries little signal. H2H is shown as context only.
          </li>
          <li>
            <strong>No odds comparison.</strong> By design, as above — independence at the cost of not
            being able to identify value.
          </li>
          <li>
            <strong>Cold-start weakness.</strong> Newly promoted teams and early-season fixtures have
            thin history. The data-quality score reflects this, and the confidence band drops
            accordingly.
          </li>
        </ul>
      </section>

      <section className="doc-cta">
        <h2>See it on real fixtures</h2>
        <p>Accounts are reviewed by a Super Admin before they go live.</p>
        <div className="landing-cta-row">
          <Link className="btn btn-lg" to="/register">
            Request access
          </Link>
          <Link className="btn ghost btn-lg" to="/responsible">
            Read the responsible-use note
          </Link>
        </div>
      </section>
    </PublicShell>
  );
}
