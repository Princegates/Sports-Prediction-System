import { useEffect, useState } from "react";
import { fetchLineup, fetchNews } from "../api";
import type { InjuredPlayer, MatchLineups, TeamLineup, TeamNews } from "../types";

interface Props {
  matchId: number;
  homeName: string;
  awayName: string;
}

function InjuryList({ players }: { players: InjuredPlayer[] }) {
  if (players.length === 0) return <p className="sub" style={{ margin: 0 }}>No injuries or suspensions reported.</p>;
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
      {players.map((p) => {
        const out = p.type !== "Questionable";
        return (
          <li key={p.name} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <span className={`status-tag ${out ? "suspended" : "pending"}`}>{out ? "Out" : "Doubt"}</span>
            <strong>{p.name}</strong>
            {p.reason && <span className="sub">{p.reason}</span>}
          </li>
        );
      })}
    </ul>
  );
}

function Lineup({ lineup }: { lineup: TeamLineup | null }) {
  if (!lineup) return <p className="sub" style={{ margin: 0 }}>Not available for this side.</p>;
  return (
    <div>
      <p style={{ margin: "0 0 8px", fontSize: 13 }}>
        {lineup.formation && <strong>{lineup.formation}</strong>}
        {lineup.coach && <span className="sub">{lineup.formation ? " · " : ""}Coach {lineup.coach}</span>}
      </p>
      <ol style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 4 }}>
        {lineup.start_xi.map((p) => (
          <li key={`${p.number}-${p.name}`} style={{ display: "flex", gap: 8 }}>
            <span className="tabular-nums" style={{ width: 24, textAlign: "right", color: "var(--text-muted)" }}>
              {p.number ?? ""}
            </span>
            <span>{p.name}</span>
            {p.pos && <span className="sub">{p.pos}</span>}
          </li>
        ))}
      </ol>
      {lineup.substitutes.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary className="sub" style={{ cursor: "pointer" }}>Substitutes ({lineup.substitutes.length})</summary>
          <p className="sub" style={{ margin: "6px 0 0" }}>
            {lineup.substitutes.map((p) => (p.number ? `${p.number} ${p.name}` : p.name)).join(" · ")}
          </p>
        </details>
      )}
    </div>
  );
}

function updated(at: string | null): string | null {
  if (!at) return null;
  // The backend sends naive UTC; without a zone the browser would read it as local time.
  const when = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(at) ? at : `${at}Z`);
  return `Updated ${when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`;
}

/**
 * Injuries and confirmed lineups for one match, from API-Football. Loaded only
 * when this tab is opened -- the backend caches each answer and spends a
 * request from a shared daily budget to refresh it (see app/team_news.py), so
 * merely viewing a match never costs one.
 */
export function TeamNewsPanel({ matchId, homeName, awayName }: Props) {
  const [news, setNews] = useState<TeamNews | null>(null);
  const [lineups, setLineups] = useState<MatchLineups | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setNews(null);
    setLineups(null);
    setFailed(false);
    fetchNews(matchId)
      .then((r) => !cancelled && setNews(r))
      .catch(() => !cancelled && setFailed(true));
    fetchLineup(matchId)
      .then((r) => !cancelled && setLineups(r))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [matchId]);

  if (failed && !news && !lineups) {
    return <div className="card card-pad"><p className="sub" style={{ margin: 0 }}>Team news couldn't be loaded right now.</p></div>;
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="card card-pad">
        <div className="section-header" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>Injuries &amp; suspensions</h3>
          {news?.updated_at && <span className="meta">{updated(news.updated_at)}</span>}
        </div>
        {news === null && <p className="badge-neutral">Loading team news…</p>}
        {news?.status === "unavailable" && <p className="sub" style={{ margin: 0 }}>{news.reason}</p>}
        {news?.status === "ok" && (
          <div className="two-col">
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: 13.5 }}>{homeName}</h4>
              <InjuryList players={news.home} />
            </div>
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: 13.5 }}>{awayName}</h4>
              <InjuryList players={news.away} />
            </div>
          </div>
        )}
      </div>

      <div className="card card-pad">
        <div className="section-header" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>Confirmed lineups</h3>
          {lineups?.updated_at && lineups.status === "ok" && <span className="meta">{updated(lineups.updated_at)}</span>}
        </div>
        {lineups === null && <p className="badge-neutral">Checking for lineups…</p>}
        {lineups && lineups.status !== "ok" && <p className="sub" style={{ margin: 0 }}>{lineups.reason}</p>}
        {lineups?.status === "ok" && (
          <div className="two-col">
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: 13.5 }}>{homeName}</h4>
              <Lineup lineup={lineups.home} />
            </div>
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: 13.5 }}>{awayName}</h4>
              <Lineup lineup={lineups.away} />
            </div>
          </div>
        )}
      </div>
      <p className="meta" style={{ margin: 0 }}>Source: API-Football. Team news can change right up to kickoff.</p>
    </div>
  );
}
