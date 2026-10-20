import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchLanding } from "../api";
import { FixtureTeaserRow } from "../components/FixtureTeaserRow";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { TeamCrest } from "../components/TeamCrest";
import { chanceLabel } from "../lib/odds";
import { fixtureSlug } from "../lib/slug";
import { usePageStructuredData } from "../lib/pageMeta";
import { useTilt } from "../lib/useTilt";
import { whatsappLink } from "../lib/whatsapp";
import type { Landing, TrackRecord } from "../types";

/**
 * Public landing page.
 *
 * Built around one question a visitor is really asking -- "do these picks
 * actually land?" -- so the hero leads with the model's own graded record
 * rather than its architecture. Six sections: hero + record, today's free
 * pick, the best-rated upcoming fixtures, what the confidence rating is
 * worth, the AI Generation demo, then price and answers.
 *
 * Every figure comes from GET /api/public/landing (one call; it used to be
 * seven). The build's prerender step bakes that response into the page as
 * a hidden data attribute (#landing-data below), so a visitor sees real
 * numbers immediately and keeps them even if the backend is slow to wake.
 */

const ORGANIZATION_SCHEMA = {
  "@type": "Organization",
  name: "Socca Intelligence",
  url: "https://soccaintel.com/",
  logo: "https://soccaintel.com/brand/icon-512.png",
  description: "Football and soccer predictions with a public, graded track record, across 50+ leagues.",
};

// AI Generation demo -- the wizard's real controls with nothing fetched
// behind them; clicking Generate leads to sign-up.
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

const BAND_ORDER = ["HIGH", "MEDIUM", "LOW"];
const BAND_LABEL: Record<string, string> = { HIGH: "High confidence", MEDIUM: "Medium", LOW: "Low" };

/** The prerendered copy of the last landing response, if this page was
 * served from the build's snapshot. Read during the first render, before
 * React replaces the prerendered DOM. */
function bakedLanding(): Landing | null {
  try {
    const raw = document.getElementById("landing-data")?.getAttribute("data-json");
    return raw ? (JSON.parse(raw) as Landing) : null;
  } catch {
    return null;
  }
}

function pct(n: number | undefined): string {
  return `${Math.round((n ?? 0) * 1000) / 10}%`;
}

function sinceLabel(since: string | null | undefined): string {
  if (!since) return "";
  return new Date(since).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function band(record: TrackRecord, key: string) {
  return record.by_confidence?.find((b) => b.confidence === key);
}

function kickoffLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function Welcome() {
  const [landing, setLanding] = useState<Landing | null>(bakedLanding);
  const [openFaq, setOpenFaq] = useState<number | null>(0);
  const [demoLeagues, setDemoLeagues] = useState<string[]>(["English Premier League"]);
  const [demoMarkets, setDemoMarkets] = useState<string[]>(["Match Result", "Both Teams To Score"]);
  const [demoLegs, setDemoLegs] = useState(6);
  const [demoAccuracy, setDemoAccuracy] = useState(65);
  const [demoGenerateClicked, setDemoGenerateClicked] = useState(false);
  const heroTilt = useTilt<HTMLDivElement>();

  useEffect(() => {
    // A failed refresh keeps whatever is already on screen (the baked
    // snapshot), rather than blanking real numbers.
    fetchLanding().then(setLanding).catch(() => {});
  }, []);

  const branding = landing?.branding ?? null;
  const record = landing?.track_record ?? null;
  const stats = landing?.stats ?? null;
  const pick = landing?.pick_of_the_day?.has_data ? landing.pick_of_the_day : null;
  const adminRecord = landing?.admin_pick_record?.has_data && landing.admin_pick_record.settled_count >= 5 ? landing.admin_pick_record : null;
  const fixtures = landing?.fixtures ?? [];
  const price = branding?.pricing_label || null;
  const perDay = branding?.price_per_day || null;
  const trialDays = branding?.trial_days ?? 0;
  const whatsapp = branding?.contact_whatsapp || null;
  const channel = branding?.whatsapp_channel_url || null;
  const high = record?.has_data ? band(record, "HIGH") : undefined;
  const nf = new Intl.NumberFormat();

  const faqs = [
    {
      q: "How does the free trial work?",
      a:
        trialDays > 0
          ? `Create an account and confirm your email: you get ${trialDays} day${trialDays === 1 ? "" : "s"} of full access, with no card and no code. After that you stay on the free tier (one headline pick per league) until you buy an access code.`
          : "Creating an account is free. The free tier shows one headline pick per league; an access code unlocks everything.",
    },
    {
      q: "How do I pay?",
      a: `Payment happens outside the site: message our team on WhatsApp and pay by mobile money or bank transfer. Once it's confirmed, you get an access code by email or WhatsApp, enter it on the Access page, and everything unlocks for the code's duration.${price ? ` Access is ${price}${perDay ? ` (${perDay})` : ""}.` : ""}`,
    },
    {
      q: "How accurate is it, honestly?",
      a:
        record?.has_data && high
          ? `Every call is graded against the real result. Since ${sinceLabel(record.since)}, ${pct(record.hit_rate)} of all ${nf.format(record.graded_predictions ?? 0)} graded calls landed, and ${pct(high.hit_rate)} of the high-confidence ones. For context, professional bookmakers land around 53-55% on match results. Anyone claiming far more than that deserves suspicion.`
          : "Every call is graded against the real result once the match finishes. For context, professional bookmakers land around 53-55% on match results; anyone claiming far more than that deserves suspicion.",
    },
    {
      q: "Are these predictions guaranteed?",
      a: "No, and nothing here will ever say otherwise. Every prediction is a probability with a confidence rating. A 90% call still loses about one time in ten, and losses come in clusters.",
    },
    {
      q: "Which leagues do you cover?",
      a: stats
        ? `${stats.leagues_covered} competitions, including the Premier League, La Liga, Serie A, the Bundesliga, Ligue 1 and the Champions League, with ${nf.format(stats.upcoming_fixtures)} upcoming fixtures loaded right now.`
        : "50+ competitions, including the Premier League, La Liga, Serie A, the Bundesliga, Ligue 1 and the Champions League.",
    },
  ];

  usePageStructuredData({
    "@context": "https://schema.org",
    "@graph": [
      ORGANIZATION_SCHEMA,
      {
        "@type": "FAQPage",
        mainEntity: faqs.map((f) => ({ "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a } })),
      },
    ],
  });

  function toggleDemo(list: string[], setList: (next: string[]) => void, value: string) {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  const trialCta = trialDays > 0 ? `Start your ${trialDays}-day free trial` : "Create a free account";

  return (
    <PublicShell
      title="AI Football & Soccer Predictions — Socca Intelligence"
      description="Football and soccer predictions with a public, graded track record: every call rated for confidence and checked against the real result. 50+ leagues. Free trial, no card."
    >
      {/* Snapshot for the next visitor's first paint -- see bakedLanding. */}
      {landing && <div hidden id="landing-data" data-json={JSON.stringify(landing)} />}

      {/* ---------------- Hero ---------------- */}
      <section className="landing-hero">
        <div className="landing-hero-copy">
          <span className="eyebrow">Football &amp; Soccer Predictions</span>
          <h1>
            Football predictions
            <span className="hero-gradient"> with a public track record</span>
          </h1>
          <p className="lede">
            Every match gets a probability and a confidence rating, and every call is checked against the real
            result once the final whistle goes. You can see exactly how often each rating lands before you pay
            a cedi.
          </p>
          <div className="landing-cta-row">
            <Link className="btn btn-lg" to="/register">
              {trialCta}
            </Link>
            <Link className="btn ghost btn-lg" to="/fixtures">
              See today's fixtures
            </Link>
          </div>
          <p className="landing-cta-note">
            {trialDays > 0 ? "No card, no code, about 30 seconds." : "Free to sign up."}
            {price && (
              <>
                {" "}
                After that, <strong>{price}</strong>
                {perDay && <> ({perDay})</>}.
              </>
            )}{" "}
            <Link to="/pricing">How paying works</Link>
          </p>
        </div>

        <div className="landing-hero-visual tilt-card" {...heroTilt}>
          <Mascot pose="thinking" size={92} />
          <div className="hero-panel">
            <div className="hero-panel-head">
              <span className="tag">Graded results</span>
              {record?.has_data && <span className="meta">since {sinceLabel(record.since)}</span>}
            </div>
            {record?.has_data && high ? (
              <>
                <div className="hero-panel-body">
                  <div className="hero-panel-metric">
                    <span className="hero-panel-number">{pct(high.hit_rate)}</span>
                    <span>of high-confidence calls landed ({nf.format(high.graded)} graded)</span>
                  </div>
                  <div className="hero-panel-rows">
                    {BAND_ORDER.map((key) => {
                      const b = band(record, key);
                      return b ? (
                        <div key={key}>
                          <span>{BAND_LABEL[key]}</span>
                          <span>
                            {pct(b.hit_rate)} of {nf.format(b.graded)}
                          </span>
                        </div>
                      ) : null;
                    })}
                  </div>
                </div>
                <p className="hero-panel-foot">
                  {pct(record.hit_rate)} across all {nf.format(record.graded_predictions ?? 0)} calls. Every
                  prediction made before kickoff, graded on the real score -- wins and losses.
                </p>
              </>
            ) : (
              <div className="hero-panel-body">
                <p style={{ margin: 0 }}>
                  Results appear here as matches finish -- every call is graded against the real score, wins and
                  losses alike.
                </p>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ---------------- Today's free pick ---------------- */}
      {pick && pick.fixture && (
        <section className="landing-section">
          <div className="potd card card-pad">
            <div className="potd-head">
              <span className="eyebrow">Today's free pick</span>
              <span className="confidence-tag high">High confidence</span>
            </div>
            <Link to={`/predict/${fixtureSlug(pick.fixture)}`} className="potd-match">
              <TeamCrest name={pick.fixture.home_team} logoUrl={pick.fixture.home_logo_url} size={28} />
              <span>
                {pick.fixture.home_team} <span className="vs-divider">v</span> {pick.fixture.away_team}
              </span>
              <TeamCrest name={pick.fixture.away_team} logoUrl={pick.fixture.away_logo_url} size={28} />
            </Link>
            <p className="meta" style={{ margin: 0 }}>
              {pick.fixture.league} · {kickoffLabel(pick.fixture.kickoff)}
            </p>
            <div className="potd-call">
              <span className="potd-selection">
                {pick.market}: <strong>{pick.selection}</strong>
              </span>
              <span className="potd-prob">{pct(pick.probability ?? 0)}</span>
            </div>
            <p className="meta" style={{ margin: 0 }}>
              Our strongest call kicking off soon -- {chanceLabel(pick.probability ?? 0)} chance by the model's own
              numbers, so it can still lose. One pick like this is free every day; members see every match.
            </p>
            <div className="landing-cta-row" style={{ marginTop: 4 }}>
              <Link className="btn" to="/register">
                {trialCta}
              </Link>
              {channel && (
                <a className="btn ghost" href={channel} target="_blank" rel="noreferrer noopener">
                  Get the daily pick on WhatsApp
                </a>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ---------------- Best-rated upcoming fixtures ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>Coming up, best-rated first</h2>
          <p>
            {stats
              ? `${stats.leagues_covered} leagues · ${nf.format(stats.matches_analyzed)} matches analysed · ${nf.format(stats.upcoming_fixtures)} fixtures loaded. `
              : ""}
            Open any match for recent form and past meetings; the pick and its probability are for members.
          </p>
        </div>
        {fixtures.length > 0 ? (
          <div className="fixture-teaser-list">
            {fixtures.map((f) => (
              <FixtureTeaserRow key={f.match_id} fixture={f} />
            ))}
          </div>
        ) : (
          <p className="meta">Fixtures for the next two days appear here once they're loaded.</p>
        )}
        <p style={{ marginTop: 12 }}>
          <Link to="/fixtures">See every upcoming fixture</Link>
        </p>
      </section>

      {/* ---------------- What the confidence rating is worth ---------------- */}
      {record?.has_data && (
        <section className="landing-section">
          <div className="section-head">
            <h2>Does the confidence rating mean anything?</h2>
            <p>
              It should: a high-confidence call should land more often than a low one. Here is how every graded
              call since {sinceLabel(record.since)} has done, by the rating it had before kickoff.
            </p>
          </div>
          <div className="proof-bars card card-pad">
            {BAND_ORDER.map((key) => {
              const b = band(record, key);
              if (!b) return null;
              return (
                <div key={key} className="proof-bar">
                  <span className="proof-bar-label">{BAND_LABEL[key]}</span>
                  <span className="proof-bar-track" aria-hidden>
                    <span className={`proof-bar-fill ${key.toLowerCase()}`} style={{ width: `${Math.round(b.hit_rate * 100)}%` }} />
                  </span>
                  <span className="proof-bar-value">
                    {pct(b.hit_rate)} <span className="meta">of {nf.format(b.graded)}</span>
                  </span>
                </div>
              );
            })}
            <p className="meta" style={{ margin: "6px 0 0" }}>
              For context, professional bookmakers land around 53-55% on match results.{" "}
              <Link to="/how-it-works">How the model is built and tested</Link>
            </p>
          </div>
          {adminRecord && (
            <p style={{ marginTop: 12 }}>
              Our team's own published picks, committed before kickoff: <strong>{pct(adminRecord.hit_rate)}</strong> of{" "}
              {adminRecord.won + adminRecord.lost} decided.
            </p>
          )}
        </section>
      )}

      {/* ---------------- AI Generation demo ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>Try AI Generation</h2>
          <p>
            Pick leagues, markets, how many legs and how sure each leg must be -- the same controls members use
            to build a slip from live predictions.
          </p>
        </div>

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

      {/* ---------------- Price ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>Start free, pay when it's worth it</h2>
        </div>
        <div className="plan-strip">
          <div className="plan-tile">
            <span className="plan-name">Free</span>
            <span className="plan-price">Free</span>
            <span className="plan-desc">One headline pick per league, plus today's free pick.</span>
          </div>
          {trialDays > 0 && (
            <div className="plan-tile featured">
              <span className="plan-name">Free trial</span>
              <span className="plan-price">
                {trialDays} day{trialDays === 1 ? "" : "s"}
              </span>
              <span className="plan-desc">Everything members get. No card, no code.</span>
            </div>
          )}
          <div className="plan-tile">
            <span className="plan-name">Full access</span>
            <span className="plan-price">{price ?? "Ask us"}</span>
            <span className="plan-desc">
              {perDay ? `${perDay[0].toUpperCase()}${perDay.slice(1)}. ` : ""}Every match, every market, AI Generation and live
              updates.
            </span>
          </div>
        </div>
        <div className="landing-cta-row" style={{ justifyContent: "center", marginTop: 16 }}>
          <Link className="btn btn-lg" to="/register">
            {trialCta}
          </Link>
          <Link className="btn ghost btn-lg" to="/pricing">
            Pricing details
          </Link>
        </div>
      </section>

      {/* ---------------- FAQ ---------------- */}
      <section className="landing-section">
        <div className="section-head">
          <h2>Straight answers</h2>
        </div>
        <div className="faq-list">
          {faqs.map((f, i) => (
            <div key={f.q} className={`faq-item${openFaq === i ? " open" : ""}`}>
              <button className="faq-question" onClick={() => setOpenFaq(openFaq === i ? null : i)} aria-expanded={openFaq === i}>
                <span>{f.q}</span>
                <span className="faq-chevron" aria-hidden>
                  {openFaq === i ? "−" : "+"}
                </span>
              </button>
              {openFaq === i && <p className="faq-answer">{f.a}</p>}
            </div>
          ))}
        </div>
        {whatsapp && (
          <p style={{ marginTop: 12 }}>
            Something else?{" "}
            <a href={whatsappLink(whatsapp, "Hi, I have a question about Socca Intelligence.")} target="_blank" rel="noreferrer noopener">
              Ask us on WhatsApp
            </a>
            .
          </p>
        )}
      </section>

      {/* ---------------- Responsible use ---------------- */}
      <section className="landing-section responsible-section">
        <div className="responsible-card">
          <h2>Before you act on any of this</h2>
          <p>
            These are probabilities, not outcomes. A 90% call loses about one time in ten, and losses cluster.
            Never stake money you need, and never raise a stake to win back a loss. 18+.
          </p>
          <p className="responsible-links">
            Free, confidential help: call Ghana's{" "}
            <a href="https://www.moh.gov.gh/mental-health-authority/" target="_blank" rel="noreferrer noopener">
              Mental Health Authority
            </a>{" "}
            on <strong>0800 678 678</strong> (toll-free, any hour), or reach{" "}
            <a href="https://www.gamblingtherapy.org" target="_blank" rel="noreferrer noopener">
              Gambling Therapy
            </a>{" "}
            online. <Link to="/responsible">More on using this responsibly</Link>
          </p>
        </div>
      </section>
    </PublicShell>
  );
}
