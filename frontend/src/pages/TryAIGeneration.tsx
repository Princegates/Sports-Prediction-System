import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchBranding } from "../api";
import { PublicShell } from "../components/PublicShell";
import type { Branding } from "../types";

/**
 * Public AI Generation demo -- its own page (and top-nav tab), not a
 * section embedded in Welcome.tsx's scroll, so a first-time visitor can
 * reach it directly without scrolling past the track record and free picks
 * first, and it stays discoverable from every other public page via the nav.
 *
 * The wizard's real controls, with nothing fetched behind them -- clicking
 * Generate leads to sign-up, since every leg in a real slip comes from live
 * predictions, not a demo.
 */

const DEMO_LEAGUES = [
  "English Premier League", "Spanish La Liga", "German Bundesliga", "Italian Serie A",
  "French Ligue 1", "UEFA Champions League",
];
const DEMO_MARKETS = [
  "Match Result", "Both Teams To Score", "Total Goals 2.5", "Double Chance", "Draw No Bet", "Correct Score",
];
const DEMO_LEG_OPTIONS = [4, 6, 8, 10];
const DEMO_ACCURACY_OPTIONS = [
  { value: 50, label: "50%+" },
  { value: 65, label: "65%+" },
  { value: 75, label: "75%+" },
  { value: 85, label: "85%+" },
];

export function TryAIGeneration() {
  const [branding, setBranding] = useState<Branding | null>(null);
  const [demoLeagues, setDemoLeagues] = useState<string[]>(["English Premier League"]);
  const [demoMarkets, setDemoMarkets] = useState<string[]>(["Match Result", "Both Teams To Score"]);
  const [demoLegs, setDemoLegs] = useState(6);
  const [demoAccuracy, setDemoAccuracy] = useState(65);
  const [demoGenerateClicked, setDemoGenerateClicked] = useState(false);

  useEffect(() => {
    fetchBranding().then(setBranding).catch(() => setBranding(null));
  }, []);

  const trialDays = branding?.trial_days ?? 0;
  const trialCta = trialDays > 0 ? `Start your ${trialDays}-day free trial` : "Create a free account";

  function toggleDemo(list: string[], setList: (next: string[]) => void, value: string) {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  return (
    <PublicShell
      title="Try AI Generation — Socca Intelligence"
      description="Pick leagues, markets, how many legs and how sure each leg must be -- the same controls members use to build a slip from live predictions. Free to try, no account needed."
    >
      <section className="doc-hero">
        <span className="eyebrow">AI Generation</span>
        <h1>Try AI Generation</h1>
        <p className="lede">
          Pick leagues, markets, how many legs and how sure each leg must be -- the same controls members use
          to build a slip from live predictions.
        </p>
      </section>

      <section className="landing-section">
        <div className="card card-pad" style={{ maxWidth: 640, margin: "0 auto" }}>
          <div className="meta" style={{ marginBottom: 6 }}>
            Leagues
          </div>
          <div className="filter-bar" style={{ marginBottom: 14 }}>
            {DEMO_LEAGUES.map((l) => (
              <button
                key={l}
                type="button"
                className={`filter-chip${demoLeagues.includes(l) ? " active" : ""}`}
                onClick={() => toggleDemo(demoLeagues, setDemoLeagues, l)}
              >
                {l}
              </button>
            ))}
          </div>

          <div className="meta" style={{ marginBottom: 6 }}>
            Markets
          </div>
          <div className="filter-bar" style={{ marginBottom: 14 }}>
            {DEMO_MARKETS.map((m) => (
              <button
                key={m}
                type="button"
                className={`filter-chip${demoMarkets.includes(m) ? " active" : ""}`}
                onClick={() => toggleDemo(demoMarkets, setDemoMarkets, m)}
              >
                {m}
              </button>
            ))}
          </div>

          <div className="meta" style={{ marginBottom: 6 }}>
            Number of legs
          </div>
          <div className="filter-bar" style={{ marginBottom: 14 }}>
            {DEMO_LEG_OPTIONS.map((n) => (
              <button key={n} type="button" className={`filter-chip${demoLegs === n ? " active" : ""}`} onClick={() => setDemoLegs(n)}>
                {n}
              </button>
            ))}
          </div>

          <div className="meta" style={{ marginBottom: 6 }}>
            Minimum chance per leg
          </div>
          <div className="filter-bar">
            {DEMO_ACCURACY_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`filter-chip${demoAccuracy === o.value ? " active" : ""}`}
                onClick={() => setDemoAccuracy(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        <div style={{ textAlign: "center", marginTop: 16 }}>
          {demoGenerateClicked ? (
            <div className="state-card" style={{ maxWidth: 420, margin: "0 auto" }}>
              <p style={{ margin: "0 0 12px" }}>
                Generating needs an account -- every leg comes from live predictions, not a demo.
                {trialDays > 0 ? ` Your ${trialDays}-day free trial includes it.` : ""}
              </p>
              <Link className="btn btn-lg" to="/register">
                {trialCta}
              </Link>
            </div>
          ) : (
            <button className="btn btn-lg" onClick={() => setDemoGenerateClicked(true)}>
              Generate selections
            </button>
          )}
        </div>
      </section>
    </PublicShell>
  );
}
