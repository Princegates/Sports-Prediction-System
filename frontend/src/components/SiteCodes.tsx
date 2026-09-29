import { useEffect, useState } from "react";
import { createSiteCodes, fetchBettingSites } from "../api";
import type { BetCodeCriteria, BetCodeLeg, BettingSite, SiteCode } from "../types";
import { CopyButton } from "./CopyButton";

interface Props {
  legs: BetCodeLeg[];
  criteria: BetCodeCriteria;
}

/**
 * Turns the slip on screen into a real booking code on each chosen betting
 * site -- the site issues it, this only asks. Renders nothing until at least
 * one site's connection is built, so members are never offered a button that
 * can only fail.
 */
export function SiteCodes({ legs, criteria }: Props) {
  const [sites, setSites] = useState<BettingSite[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [codes, setCodes] = useState<SiteCode[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchBettingSites()
      .then((all) => {
        if (cancelled) return;
        const connected = all.filter((s) => s.connected);
        setSites(connected);
        setChosen(connected.map((s) => s.key));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // A different slip means the old codes no longer describe it.
  const legsKey = legs.map((l) => `${l.match_id}|${l.market}|${l.selection}`).join(",");
  useEffect(() => setCodes(null), [legsKey]);

  if (sites.length === 0) return null;

  function toggle(key: string) {
    setChosen((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  async function handleGenerate() {
    setBusy(true);
    setError(null);
    try {
      const slip = await createSiteCodes(criteria, legs, chosen);
      setCodes(slip.site_codes);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  function matchName(matchId: number): string {
    const leg = legs.find((l) => l.match_id === matchId);
    return leg ? `${leg.home_team} vs ${leg.away_team}` : `match ${matchId}`;
  }

  return (
    <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
      <div className="meta" style={{ marginBottom: 8 }}>
        Booking codes -- each site issues its own; odds are that site's, not the prices above
      </div>
      <div className="filter-bar">
        {sites.map((s) => (
          <button
            key={s.key}
            className={`filter-chip${chosen.includes(s.key) ? " active" : ""}`}
            onClick={() => toggle(s.key)}
          >
            {s.name}
          </button>
        ))}
      </div>
      <button className="btn" style={{ marginTop: 12 }} onClick={handleGenerate} disabled={busy || chosen.length === 0}>
        {busy ? "Asking the sites…" : `Get booking code${chosen.length === 1 ? "" : "s"}`}
      </button>
      {error && <p className="auth-error">{error}</p>}

      {codes && (
        <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
          {codes.map((c) => (
            <div key={c.site} className="card card-pad">
              <div className="section-header" style={{ marginBottom: 6 }}>
                <strong>{c.name}</strong>
                {c.status === "code_ready" && c.code ? (
                  <span className="tabular-nums" style={{ fontSize: 22, fontWeight: 800, letterSpacing: 2 }}>
                    {c.code}
                  </span>
                ) : (
                  <span className="sub">No code</span>
                )}
              </div>
              {c.message && <p className="setting-note">{c.message}</p>}
              {c.unavailable_match_ids.length > 0 && (
                <p className="sub">Not included: {c.unavailable_match_ids.map(matchName).join(", ")}</p>
              )}
              {c.status === "code_ready" && c.code && (
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 6 }}>
                  <CopyButton text={c.code} label="Copy code" />
                  {c.link && (
                    <a className="btn ghost" href={c.link} target="_blank" rel="noopener noreferrer">
                      Open on {c.name}
                    </a>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
