import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchLive, fetchMatches } from "../api";
import { leagueLabel, useLeague } from "../components/AppShell";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { TeamCrest } from "../components/TeamCrest";
import { useAuth } from "../lib/AuthContext";
import type { LivePrediction, MatchSummary } from "../types";

/** Live scores, laid out the way people expect from a livescore site: one
 * day at a time, grouped by competition, a compact row per match with its
 * clock (minute, FT or kickoff time) on the left and the score on the
 * right. Live rows add the model's current most-likely outcome, which is
 * the one thing a plain livescore site can't show. */

type Day = "yesterday" | "today" | "tomorrow";
type Filter = "all" | "live" | "finished" | "upcoming";

const DAYS: { key: Day; label: string; offset: number }[] = [
  { key: "yesterday", label: "Yesterday", offset: -1 },
  { key: "today", label: "Today", offset: 0 },
  { key: "tomorrow", label: "Tomorrow", offset: 1 },
];

// The backend re-syncs live matches every 5 minutes; checking every minute
// means a new goal shows up within a minute of the server knowing about it.
const REFRESH_MS = 60_000;

/** YYYY-MM-DD for a day relative to today -- the /api/matches `date` filter. */
function isoDay(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function kind(m: MatchSummary): Exclude<Filter, "all"> | "unknown" {
  if (m.is_live) return "live";
  if (m.status === "FINISHED") return "finished";
  if (m.status === "SCHEDULED") return "upcoming";
  // LIVE with no recent confirmation from the sync job (see is_live), or a
  // postponed/cancelled status -- shown, but never claimed as live.
  return "unknown";
}

interface LeagueGroup {
  league: string;
  matches: MatchSummary[];
  liveCount: number;
}

function groupByLeague(matches: MatchSummary[]): LeagueGroup[] {
  const byLeague = new Map<string, MatchSummary[]>();
  for (const m of matches) {
    const list = byLeague.get(m.league) ?? [];
    list.push(m);
    byLeague.set(m.league, list);
  }
  return [...byLeague.entries()]
    .map(([league, list]) => ({
      league,
      matches: list.sort((a, b) => a.date.localeCompare(b.date)),
      liveCount: list.filter((m) => m.is_live).length,
    }))
    // Competitions with something live first, then alphabetical.
    .sort((a, b) => Number(b.liveCount > 0) - Number(a.liveCount > 0) || a.league.localeCompare(b.league));
}

function StatusCell({ match, minute }: { match: MatchSummary; minute: number | undefined }) {
  const k = kind(match);
  if (k === "live") {
    return (
      <span className="ls-status live" aria-label={minute ? `Live, minute ${minute}` : "Live"}>
        <span className="live-dot" aria-hidden />
        {minute ? `${minute}'` : "LIVE"}
      </span>
    );
  }
  if (k === "finished") return <span className="ls-status">FT</span>;
  if (k === "upcoming") {
    return (
      <span className="ls-status">
        {new Date(match.date).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false })}
      </span>
    );
  }
  return <span className="ls-status muted">{match.status === "LIVE" ? "–" : match.status.slice(0, 4)}</span>;
}

function MatchRow({ match, live }: { match: MatchSummary; live: LivePrediction | undefined }) {
  const k = kind(match);
  const showScore = k === "live" || k === "finished" || (k === "unknown" && match.home_score != null);
  const home = match.home_score ?? 0;
  const away = match.away_score ?? 0;
  const homeWon = k === "finished" && home > away;
  const awayWon = k === "finished" && away > home;

  return (
    <Link to={`/app/match/${match.id}${k === "live" ? "?tab=Live" : ""}`} className={`ls-row${k === "live" ? " is-live" : ""}`}>
      <StatusCell match={match} minute={live?.minute} />
      <span className="ls-teams">
        <span className={`ls-team${homeWon ? " won" : ""}`}>
          <TeamCrest name={match.home_team.name} logoUrl={match.home_team.logo_url} />
          <span className="ls-team-name">{match.home_team.name}</span>
        </span>
        <span className={`ls-team${awayWon ? " won" : ""}`}>
          <TeamCrest name={match.away_team.name} logoUrl={match.away_team.logo_url} />
          <span className="ls-team-name">{match.away_team.name}</span>
        </span>
      </span>
      {k === "live" && live && (
        <span className="ls-model" title="The model's most likely outcome right now, recalculated from the live score">
          <span className="ls-model-label">Model</span>
          {live.global_outcome.selection} {(live.global_outcome.probability * 100).toFixed(0)}%
        </span>
      )}
      <span className={`ls-score${k === "live" ? " live" : ""}`}>
        {showScore ? (
          <>
            <span className={homeWon ? "won" : undefined}>{home}</span>
            <span className={awayWon ? "won" : undefined}>{away}</span>
          </>
        ) : (
          <>
            <span className="muted">–</span>
            <span className="muted">–</span>
          </>
        )}
      </span>
    </Link>
  );
}

export function Live() {
  const { league } = useLeague();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [day, setDay] = useState<Day>("today");
  const [filter, setFilter] = useState<Filter>("all");
  const [matches, setMatches] = useState<MatchSummary[] | null>(null);
  const [liveState, setLiveState] = useState<Record<number, LivePrediction>>({});
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(
    async (isRefresh: boolean) => {
      const offset = DAYS.find((d) => d.key === day)!.offset;
      try {
        const requests = [fetchMatches({ league: league || undefined, date: isoDay(offset) })];
        // Today also takes every genuinely live match regardless of its
        // calendar date, so a late kickoff running past midnight stays put.
        if (day === "today") requests.push(fetchMatches({ league: league || undefined, status: "LIVE" }));
        const results = await Promise.all(requests);
        const byId = new Map<number, MatchSummary>();
        for (const m of results.flat()) byId.set(m.id, m);
        const merged = [...byId.values()];

        const liveMatches = merged.filter((m) => m.is_live);
        const histories = await Promise.all(liveMatches.map((m) => fetchLive(m.id).catch(() => [] as LivePrediction[])));
        const latest: Record<number, LivePrediction> = {};
        liveMatches.forEach((m, i) => {
          const history = histories[i];
          if (history.length) latest[m.id] = history[history.length - 1];
        });

        setMatches(merged);
        setLiveState(latest);
        setUpdatedAt(new Date());
        setError(null);
      } catch (err) {
        // A failed background refresh keeps the last good board on screen.
        if (!isRefresh) setError(err instanceof Error ? err.message : String(err));
      }
    },
    [day, league],
  );

  useEffect(() => {
    setMatches(null);
    setError(null);
    void load(false);
    if (day !== "today") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [day, load]);

  const counts = useMemo(() => {
    const c = { all: 0, live: 0, finished: 0, upcoming: 0 };
    for (const m of matches ?? []) {
      c.all += 1;
      const k = kind(m);
      if (k !== "unknown") c[k] += 1;
    }
    return c;
  }, [matches]);

  const groups = useMemo(() => {
    const visible = (matches ?? []).filter((m) => filter === "all" || kind(m) === filter);
    return groupByLeague(visible);
  }, [matches, filter]);

  const FILTERS: { key: Filter; label: string }[] = [
    { key: "all", label: "All" },
    { key: "live", label: "Live" },
    { key: "finished", label: "Finished" },
    { key: "upcoming", label: "Upcoming" },
  ];

  return (
    <div>
      <div className="section-header">
        <h2>Live scores</h2>
        <span className="meta">
          {leagueLabel(league)}
          {day === "today" && updatedAt && <> · updated {updatedAt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</>}
        </span>
      </div>

      <div className="ls-toolbar">
        <div className="ls-days" role="tablist" aria-label="Day">
          {DAYS.map((d) => (
            <button
              key={d.key}
              type="button"
              role="tab"
              aria-selected={day === d.key}
              className={`ls-day${day === d.key ? " active" : ""}`}
              onClick={() => setDay(d.key)}
            >
              {d.label}
            </button>
          ))}
        </div>
        <div className="ls-filters" role="tablist" aria-label="Status">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              role="tab"
              aria-selected={filter === f.key}
              className={`ls-filter${filter === f.key ? " active" : ""}${f.key === "live" ? " live" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.key === "live" && counts.live > 0 && <span className="live-dot" aria-hidden />}
              {f.label}
              {matches !== null && <span className="ls-count">{counts[f.key]}</span>}
            </button>
          ))}
        </div>
      </div>

      {error && <ErrorState message={error} />}
      {!error && matches === null && (
        <div className="ls-board" aria-busy="true">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="ls-row skeleton-row" />
          ))}
        </div>
      )}
      {!error && matches !== null && groups.length === 0 && (
        <EmptyState
          icon="●"
          title={
            filter === "live"
              ? "Nothing is being played right now."
              : `No ${filter === "all" ? "" : `${filter} `}matches ${day === "today" ? "today" : day} for ${leagueLabel(league)}.`
          }
          hint={
            filter === "live"
              ? "Matches appear here within a few minutes of kickoff, with the score and minute kept up to date."
              : "Try another day, or pick a different league from the selector at the top."
          }
        />
      )}
      {!error && groups.length > 0 && (
        <div className="ls-board">
          {groups.map((g) => (
            <section key={g.league} className="ls-league">
              <header className="ls-league-head">
                <span className="ls-league-name">{g.league}</span>
                {g.liveCount > 0 && <span className="ls-league-live">{g.liveCount} live</span>}
              </header>
              {g.matches.map((m) => (
                <MatchRow key={m.id} match={m} live={liveState[m.id]} />
              ))}
            </section>
          ))}
        </div>
      )}

      {user?.role === "superadmin" && (
        <div className="card card-pad" style={{ marginTop: 24 }}>
          <h3 style={{ marginTop: 0, fontSize: 15 }}>Live engine simulator</h3>
          <p style={{ marginTop: 0, color: "var(--text-secondary)", fontSize: 13.5 }}>
            Super Admin only. Open any scheduled match's <strong>Live</strong> tab and push simulated events to see the
            Global Most-Likely Outcome recalculate. Simulations aren't saved and nobody else sees them.
          </p>
          <button
            type="button"
            className="btn ghost btn-sm"
            disabled={!matches?.some((m) => kind(m) === "upcoming")}
            onClick={() => {
              const next = matches?.find((m) => kind(m) === "upcoming");
              if (next) navigate(`/app/match/${next.id}?tab=Live`);
            }}
          >
            Open the next upcoming match
          </button>
        </div>
      )}
    </div>
  );
}
