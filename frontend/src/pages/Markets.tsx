import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createAdminPick, fetchBranding, fetchMatches, fetchOutcomes } from "../api";
import { ConfidenceTag } from "../components/MostLikelyOutcome";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { dateKeyFromIso, FixtureCalendar } from "../components/FixtureCalendar";
import { useLeague } from "../components/AppShell";
import { formatPicksForCopy, readStoredPicks, storePicks } from "../lib/myPicks";
import { useAuth } from "../lib/AuthContext";
import type { BettingOutcome, MatchSummary, OutcomesResponse } from "../types";

/**
 * Every available betting outcome, grouped by league -- laid out as a
 * bookmaker coupon: one row per match, the model's selection and
 * probability for each market sat in columns, and the markets themselves
 * switched with tabs (Match Result & Goals, Double Chance, GG/NG, Draw No
 * Bet), the same shape a SportyBet-style coupon uses. The predictions table
 * answers "what does the system say about this match"; this answers "where
 * is the best Over 2.5 this week, which BTTS calls stand out" -- comparing
 * outcomes across matches, which a per-match view can't do, without the
 * same match repeating once per market the old flat-row layout produced.
 *
 * The first four tabs pull exactly the market(s) they need -- one
 * `fetchOutcomes` call per market, at a high per-league cap -- rather than
 * one unfiltered call for every market this project knows. A match's own
 * near-certain exotic-market outcomes (a 99% Under 8.5, say) would
 * otherwise crowd genuinely useful matches out of a shared top-N cap. The
 * fifth tab, "Other Markets", keeps the original market-dropdown-driven
 * flat table for everything else this project prices (Correct Score,
 * Winning Margin, and so on) -- building fixed columns for every one of
 * those would be a lot of layout for markets few people compare side by
 * side across matches anyway.
 *
 * The 3/7/14-day chips are a rolling window from today -- fine for "what's
 * coming up", useless for "what's on the 14th". FixtureCalendar answers
 * that: it's seeded from every SCHEDULED match this league has (no
 * days-ahead cap), so it can show fixture density a season out, and
 * picking a date there computes just enough days_ahead to reach it and
 * filters both coupon views down to that one day.
 */

const DAY_OPTIONS = [3, 7, 14];
const GOAL_LINES = ["1.5", "2.5", "3.5"];
const FLOORS = [
  { value: 0, label: "Any" },
  { value: 0.5, label: "50%+" },
  { value: 0.7, label: "70%+" },
  { value: 0.85, label: "85%+" },
];

// Exported so Settings.tsx's "default tab" picker (default_market_tab) shows
// the exact same keys and labels as this page's own tab bar -- one list,
// never two that could drift apart.
export type GridTabKey = "match_result" | "double_chance" | "btts" | "draw_no_bet" | "other";

export const GRID_TABS: { key: GridTabKey; label: string }[] = [
  { key: "match_result", label: "Match Result & Goals" },
  { key: "double_chance", label: "Double Chance" },
  { key: "btts", label: "GG/NG" },
  { key: "draw_no_bet", label: "Draw No Bet" },
  { key: "other", label: "Other Markets" },
];

interface GridColumn {
  market: string;
  selection: string;
  header: string;
}

const FIXED_COLUMNS: Record<Exclude<GridTabKey, "other" | "match_result">, GridColumn[]> = {
  double_chance: [
    { market: "Double Chance", selection: "Home/Draw", header: "1X" },
    { market: "Double Chance", selection: "Home/Away", header: "12" },
    { market: "Double Chance", selection: "Draw/Away", header: "X2" },
  ],
  btts: [
    { market: "Both Teams To Score", selection: "Yes", header: "Yes" },
    { market: "Both Teams To Score", selection: "No", header: "No" },
  ],
  draw_no_bet: [
    { market: "Draw No Bet", selection: "Home", header: "Home" },
    { market: "Draw No Bet", selection: "Away", header: "Away" },
  ],
};

interface MatchRow {
  match_id: number;
  league: string;
  home_team: string;
  away_team: string;
  kickoff: string;
  cells: Map<string, BettingOutcome>;
}

function cellKey(market: string, selection: string): string {
  return `${market}::${selection}`;
}

function buildMatchRows(outcomeLists: BettingOutcome[][]): MatchRow[] {
  const rows = new Map<number, MatchRow>();
  for (const list of outcomeLists) {
    for (const o of list) {
      let row = rows.get(o.match_id);
      if (!row) {
        row = {
          match_id: o.match_id, league: o.league, home_team: o.home_team,
          away_team: o.away_team, kickoff: o.kickoff, cells: new Map(),
        };
        rows.set(o.match_id, row);
      }
      row.cells.set(cellKey(o.market, o.selection), o);
    }
  }
  return Array.from(rows.values());
}

/** The highest-probability cell among a group of mutually-exclusive-ish
 * columns present on this row -- "the AI's own selection" for that market,
 * highlighted distinctly from the row's other probabilities. Double
 * Chance's three options aren't strictly mutually exclusive (they're
 * unions of Match Result -- see outcomes/registry.py's DOMINANT_UNION_GROUPS),
 * but "which of the three is safest" is still exactly what this highlights. */
function topKeyIn(row: MatchRow, keys: string[]): string | null {
  let best: string | null = null;
  let bestP = -1;
  for (const k of keys) {
    const o = row.cells.get(k);
    if (o && o.probability > bestP) {
      best = k;
      bestP = o.probability;
    }
  }
  return best;
}

function groupBy<T>(items: T[], keyOf: (item: T) => string): [string, T[]][] {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const key = keyOf(item);
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(item);
  }
  return Array.from(map.entries());
}

function formatDateHeading(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function Markets() {
  const { league } = useLeague();
  const { user } = useAuth();
  const navigate = useNavigate();

  const [tab, setTab] = useState<GridTabKey>("match_result");
  const [goalLine, setGoalLine] = useState("2.5");
  const [days, setDays] = useState(7);
  const [picks, setPicks] = useState<BettingOutcome[]>(readStoredPicks);
  const [featuring, setFeaturing] = useState(false);
  const tabTouchedByUser = useRef(false);

  const [calendarOpen, setCalendarOpen] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [calendarMatches, setCalendarMatches] = useState<MatchSummary[]>([]);

  useEffect(() => storePicks(picks), [picks]);

  // Every SCHEDULED fixture this league has, regardless of how far out --
  // just for the calendar's dots, never rendered as rows itself.
  useEffect(() => {
    let cancelled = false;
    fetchMatches({ league: league || undefined, status: "SCHEDULED" })
      .then((m) => !cancelled && setCalendarMatches(m))
      .catch(() => !cancelled && setCalendarMatches([]));
    return () => {
      cancelled = true;
    };
  }, [league]);

  // A picked calendar date overrides the day-range chips: just enough
  // days_ahead to include that date, then the coupon views below filter
  // down to it exactly (a date past the chips' own 14-day ceiling still
  // needs to reach the fetch).
  const effectiveDays = useMemo(() => {
    if (!selectedDate) return days;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const target = new Date(`${selectedDate}T00:00:00`);
    const diff = Math.round((target.getTime() - today.getTime()) / 86_400_000);
    return Math.max(diff + 1, 1);
  }, [selectedDate, days]);

  // Superadmin-configured default (Settings -> Betting markets -> Default
  // tab), applied once -- and only if this visitor hasn't already clicked a
  // different tab themselves before the fetch resolves.
  useEffect(() => {
    fetchBranding()
      .then((b) => {
        if (tabTouchedByUser.current) return;
        if (GRID_TABS.some((t) => t.key === b.default_market_tab)) {
          setTab(b.default_market_tab as GridTabKey);
        }
      })
      .catch(() => {});
  }, []);

  function selectTab(key: GridTabKey) {
    tabTouchedByUser.current = true;
    setTab(key);
  }

  function pickedFor(matchId: number): BettingOutcome | undefined {
    return picks.find((p) => p.match_id === matchId);
  }

  /** At most one pick per match -- clicking the already-picked outcome
   * removes it, clicking a different outcome from the same match swaps it
   * in, see lib/myPicks.ts for why. */
  function togglePick(o: BettingOutcome) {
    setPicks((prev) => {
      const existingIdx = prev.findIndex((p) => p.match_id === o.match_id);
      if (existingIdx === -1) return [...prev, o];
      const existing = prev[existingIdx];
      if (existing.market === o.market && existing.selection === o.selection) {
        return prev.filter((_, i) => i !== existingIdx);
      }
      const next = [...prev];
      next[existingIdx] = o;
      return next;
    });
  }

  async function handleFeatureAsAdminPick() {
    const label = window.prompt("Optional label for this slip (shown on every Dashboard):", "");
    if (label === null) return;
    const note = window.prompt("Optional note (shown on every Dashboard):", "") ?? undefined;

    let bookingCode: string | undefined;
    let bookingCodeBookmaker: string | undefined;
    const bookmakerInput = window.prompt(
      "Bookmaker this booking code is for (optional -- leave blank to skip):",
      "",
    );
    if (bookmakerInput) {
      const codeInput = window.prompt(`Booking code from ${bookmakerInput}:`, "");
      if (codeInput) {
        bookingCodeBookmaker = bookmakerInput;
        bookingCode = codeInput;
      }
    }

    setFeaturing(true);
    try {
      await createAdminPick({
        legs: picks.map((p) => ({ match_id: p.match_id, market: p.market, selection: p.selection })),
        label: label || undefined,
        note: note || undefined,
        priced: false,
        booking_code: bookingCode,
        booking_code_bookmaker: bookingCodeBookmaker,
      });
      window.alert("Featured on every Dashboard's Admin Picks section.");
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setFeaturing(false);
    }
  }

  // -- Coupon tabs: one outcomes fetch per market the active tab needs -----

  const gridMarkets = useMemo((): string[] => {
    if (tab === "match_result") return ["Match Result", `Total Goals ${goalLine}`];
    if (tab === "other") return [];
    return [FIXED_COLUMNS[tab][0].market];
  }, [tab, goalLine]);

  const [gridData, setGridData] = useState<OutcomesResponse[] | null>(null);
  const [gridError, setGridError] = useState<string | null>(null);

  useEffect(() => {
    if (tab === "other") return;
    let cancelled = false;
    setGridData(null);
    setGridError(null);
    Promise.all(
      gridMarkets.map((market) =>
        fetchOutcomes({ market, league: league || undefined, days_ahead: effectiveDays, min_probability: 0, limit_per_league: 500 }),
      ),
    )
      .then((results) => !cancelled && setGridData(results))
      .catch((e) => !cancelled && setGridError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, [tab, gridMarkets, league, effectiveDays]);

  const columns: GridColumn[] = useMemo(() => {
    if (tab === "match_result") {
      return [
        { market: "Match Result", selection: "Home Win", header: "1" },
        { market: "Match Result", selection: "Draw", header: "X" },
        { market: "Match Result", selection: "Away Win", header: "2" },
        { market: `Total Goals ${goalLine}`, selection: `Over ${goalLine}`, header: "Over" },
        { market: `Total Goals ${goalLine}`, selection: `Under ${goalLine}`, header: "Under" },
      ];
    }
    if (tab === "other") return [];
    return FIXED_COLUMNS[tab];
  }, [tab, goalLine]);

  // Which columns compete for the "AI selection" highlight together --
  // Match Result's 1/X/2 are one group, its Over/Under line a second,
  // separate one; every other tab's columns are a single group.
  const highlightGroups: string[][] = useMemo(() => {
    const keys = columns.map((c) => cellKey(c.market, c.selection));
    if (tab === "match_result") return [keys.slice(0, 3), keys.slice(3)];
    return [keys];
  }, [tab, columns]);

  const rowsByLeague = useMemo(() => {
    if (!gridData) return [];
    const allOutcomes = gridData.flatMap((d) => d.leagues.flatMap((l) => l.outcomes));
    let rows = buildMatchRows([allOutcomes]);
    if (selectedDate) rows = rows.filter((r) => dateKeyFromIso(r.kickoff) === selectedDate);
    const byLeague = groupBy(rows, (r) => r.league).sort(([a], [b]) => a.localeCompare(b));
    return byLeague.map(([leagueName, leagueRows]) => {
      leagueRows.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
      return { league: leagueName, dates: groupBy(leagueRows, (r) => formatDateHeading(r.kickoff)) };
    });
  }, [gridData, selectedDate]);

  // -- "Other Markets" tab: the original market-dropdown-driven flat table -

  const [otherMarket, setOtherMarket] = useState("");
  const [otherFloor, setOtherFloor] = useState(0);
  const [otherConfidence, setOtherConfidence] = useState("");
  const [otherData, setOtherData] = useState<OutcomesResponse | null>(null);
  const [otherError, setOtherError] = useState<string | null>(null);

  useEffect(() => {
    if (tab !== "other") return;
    let cancelled = false;
    setOtherData(null);
    setOtherError(null);
    fetchOutcomes({
      league: league || undefined, market: otherMarket || undefined, days_ahead: effectiveDays,
      min_probability: otherFloor, confidence: otherConfidence || undefined,
    })
      .then((d) => !cancelled && setOtherData(d))
      .catch((e) => !cancelled && setOtherError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, [tab, league, otherMarket, effectiveDays, otherFloor, otherConfidence]);

  const [knownOtherMarkets, setKnownOtherMarkets] = useState<OutcomesResponse["markets"]>([]);
  useEffect(() => {
    if (otherData && !otherMarket && !otherConfidence && otherFloor === 0) setKnownOtherMarkets(otherData.markets);
  }, [otherData, otherMarket, otherConfidence, otherFloor]);
  const otherMarketOptions = useMemo(
    () => (knownOtherMarkets.length ? knownOtherMarkets : otherData?.markets ?? []),
    [knownOtherMarkets, otherData],
  );
  const selectedOtherMarket = otherMarketOptions.find((m) => m.market === otherMarket);

  const otherLeagues = useMemo(() => {
    if (!otherData) return [];
    if (!selectedDate) return otherData.leagues;
    return otherData.leagues
      .map((g) => ({ ...g, outcomes: g.outcomes.filter((o) => dateKeyFromIso(o.kickoff) === selectedDate) }))
      .filter((g) => g.outcomes.length > 0);
  }, [otherData, selectedDate]);

  return (
    <div>
      <div className="section-header">
        <h2>Betting markets</h2>
        <span className="meta">A bookmaker-style coupon, priced by the model instead of a bookmaker.</span>
      </div>

      <div className="filter-bar" style={{ marginBottom: 10 }}>
        {GRID_TABS.map((t) => (
          <button
            key={t.key}
            className={`filter-chip${tab === t.key ? " active" : ""}`}
            onClick={() => selectTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="filter-bar" style={{ marginBottom: 14 }}>
        <button
          type="button"
          className={`filter-chip${calendarOpen ? " active" : ""}`}
          onClick={() => setCalendarOpen((v) => !v)}
        >
          📅 {selectedDate ? formatDateHeading(`${selectedDate}T00:00:00`) : "Browse by date"}
        </button>
        {selectedDate && (
          <button
            type="button"
            className="filter-chip"
            onClick={() => {
              setSelectedDate(null);
              setCalendarOpen(false);
            }}
          >
            ✕ Clear date
          </button>
        )}

        {!selectedDate &&
          DAY_OPTIONS.map((d) => (
            <button key={d} className={`filter-chip${days === d ? " active" : ""}`} onClick={() => setDays(d)}>
              {d} days
            </button>
          ))}

        {tab === "match_result" && (
          <>
            <span className="sub" style={{ marginLeft: 8 }}>
              Goals line:
            </span>
            {GOAL_LINES.map((l) => (
              <button key={l} className={`filter-chip${goalLine === l ? " active" : ""}`} onClick={() => setGoalLine(l)}>
                {l}
              </button>
            ))}
          </>
        )}
      </div>

      {calendarOpen && (
        <div className="card card-pad" style={{ marginBottom: 14, display: "inline-block" }}>
          <FixtureCalendar
            matches={calendarMatches}
            selectedDate={selectedDate}
            onSelectDate={(d) => {
              setSelectedDate(d);
              setCalendarOpen(false);
            }}
          />
        </div>
      )}

      {picks.length > 0 && (
        <div className="card card-pad" style={{ marginBottom: 20 }}>
          <div className="section-header" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>
              My picks — {picks.length} selection{picks.length === 1 ? "" : "s"}
            </h3>
            <span className="meta tabular-nums">
              {(picks.reduce((p, o) => p * o.probability, 1) * 100).toFixed(0)}% combined probability
            </span>
          </div>

          <p className="setting-note" style={{ marginBottom: 12 }}>
            Picked while browsing, not priced -- this page has no bookmaker odds attached to it, only
            the model's probability. Paste these into your own betting app yourself, or use{" "}
            <strong>AI Generation</strong> instead for a combo priced from real, stored bookmaker odds.
          </p>

          <div className="predictions-table-wrapper">
            <table className="predictions-table">
              <tbody>
                {picks.map((o) => (
                  <tr key={o.match_id}>
                    <td>
                      <div className="match-cell">
                        {o.home_team} vs {o.away_team}
                      </div>
                      <div className="sub">{o.league}</div>
                    </td>
                    <td className="sub">{o.market}</td>
                    <td>
                      <strong>{o.selection}</strong>
                    </td>
                    <td className="tabular-nums" style={{ width: 60 }}>
                      {(o.probability * 100).toFixed(0)}%
                    </td>
                    <td style={{ width: 40 }}>
                      <button
                        type="button"
                        className="btn ghost"
                        style={{ padding: "2px 8px", fontSize: 12 }}
                        onClick={() => togglePick(o)}
                        aria-label="Remove"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ marginTop: 14, display: "flex", gap: 10, flexWrap: "wrap" }}>
            <CopyButton text={formatPicksForCopy(picks)} label="Copy selections" />
            {user?.role === "superadmin" && (
              <button
                type="button"
                className="btn ghost"
                onClick={handleFeatureAsAdminPick}
                disabled={featuring}
              >
                {featuring ? "Featuring…" : "★ Feature as Admin Pick (no odds)"}
              </button>
            )}
            <button
              type="button"
              className="btn ghost"
              onClick={() =>
                navigate("/app/betcodes", {
                  state: { picks: picks.map((p) => ({ match_id: p.match_id, market: p.market, selection: p.selection })) },
                })
              }
            >
              Send {picks.length} pick{picks.length === 1 ? "" : "s"} to AI Generation for odds
            </button>
            <button type="button" className="btn ghost" onClick={() => setPicks([])}>
              Clear all
            </button>
          </div>
        </div>
      )}

      {tab !== "other" ? (
        <>
          {gridError && <ErrorState message={gridError} />}
          {!gridError && !gridData && <p className="badge-neutral">Loading outcomes…</p>}
          {!gridError && gridData && rowsByLeague.length === 0 && (
            <EmptyState
              icon="◌"
              title={
                selectedDate
                  ? `No scheduled matches with a prediction on ${formatDateHeading(`${selectedDate}T00:00:00`)}.`
                  : "No scheduled matches with a prediction in this window."
              }
            />
          )}

          {!gridError &&
            rowsByLeague.map(({ league: leagueName, dates }) => (
              <div className="card card-pad" style={{ marginBottom: 20 }} key={leagueName}>
                <div className="section-header" style={{ marginBottom: 12 }}>
                  <h3 style={{ margin: 0 }}>{leagueName}</h3>
                </div>

                {dates.map(([dateLabel, rows]) => (
                  <div key={dateLabel} style={{ marginBottom: 16 }}>
                    <div className="sub" style={{ marginBottom: 6, fontWeight: 600 }}>
                      {dateLabel}
                    </div>
                    <div className="predictions-table-wrapper">
                      <table className="predictions-table odds-grid">
                        <thead>
                          <tr>
                            <th>Match</th>
                            {columns.map((c) => (
                              <th key={c.header} className="tabular-nums" style={{ textAlign: "center" }}>
                                {c.header}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((row) => {
                            const tops = new Set(highlightGroups.map((g) => topKeyIn(row, g)).filter(Boolean) as string[]);
                            const picked = pickedFor(row.match_id);
                            return (
                              <tr key={row.match_id}>
                                <td>
                                  <div className="match-cell" style={{ cursor: "pointer" }} onClick={() => navigate(`/app/match/${row.match_id}`)}>
                                    {row.home_team} vs {row.away_team}
                                  </div>
                                  <div className="sub">{formatTime(row.kickoff)}</div>
                                </td>
                                {columns.map((c) => {
                                  const key = cellKey(c.market, c.selection);
                                  const o = row.cells.get(key);
                                  const isPicked = !!o && picked?.market === o.market && picked?.selection === o.selection;
                                  return (
                                    <td key={c.header} style={{ textAlign: "center" }}>
                                      {o ? (
                                        <button
                                          type="button"
                                          className={`odds-cell${tops.has(key) ? " ai-top" : ""}${isPicked ? " picked" : ""}`}
                                          onClick={() => togglePick(o)}
                                          title={`${o.definition} -- AI probability ${(o.probability * 100).toFixed(0)}%`}
                                        >
                                          {(o.probability * 100).toFixed(0)}%
                                        </button>
                                      ) : (
                                        <span className="odds-cell empty">—</span>
                                      )}
                                    </td>
                                  );
                                })}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </div>
            ))}
        </>
      ) : (
        <>
          <div className="filter-bar" style={{ marginBottom: 14 }}>
            <select className="filter-select" value={otherMarket} onChange={(e) => setOtherMarket(e.target.value)}>
              <option value="">All other markets</option>
              {otherMarketOptions.map((m) => (
                <option key={m.market} value={m.market}>
                  {m.market}
                </option>
              ))}
            </select>

            {FLOORS.map((f) => (
              <button
                key={f.value}
                className={`filter-chip${otherFloor === f.value ? " active" : ""}`}
                onClick={() => setOtherFloor(f.value)}
              >
                {f.label}
              </button>
            ))}

            <select className="filter-select" value={otherConfidence} onChange={(e) => setOtherConfidence(e.target.value)}>
              <option value="">Any confidence</option>
              <option value="HIGH">High only</option>
              <option value="MEDIUM">Medium only</option>
              <option value="LOW">Low only</option>
            </select>
          </div>

          {selectedOtherMarket && (
            <p className="setting-note" style={{ marginBottom: 18 }}>
              <strong>{selectedOtherMarket.market}</strong> — {selectedOtherMarket.selections.join(" · ")}.{" "}
              {selectedOtherMarket.mutually_exclusive
                ? "Exactly one of these happens, so their probabilities add up to 100%."
                : "These are not alternatives to each other — only one exact score can happen, and most matches land on none of the ones listed."}
            </p>
          )}

          {otherError && <ErrorState message={otherError} />}
          {!otherError && !otherData && <p className="badge-neutral">Loading outcomes…</p>}
          {!otherError && otherData && otherLeagues.length === 0 && (
            <EmptyState icon="◌" title="Nothing matches these filters." />
          )}

          {!otherError &&
            otherLeagues.map((leagueGroup) => (
              <div className="card card-pad" style={{ marginBottom: 20 }} key={leagueGroup.league}>
                <div className="section-header" style={{ marginBottom: 12 }}>
                  <h3 style={{ margin: 0 }}>{leagueGroup.league}</h3>
                  <span className="meta">
                    {leagueGroup.outcomes.length} outcomes · {leagueGroup.matches} matches
                  </span>
                </div>

                <div className="predictions-table-wrapper">
                  <table className="predictions-table">
                    <thead>
                      <tr>
                        <th>Match</th>
                        <th>Market</th>
                        <th>Selection</th>
                        <th>Probability</th>
                        <th>Confidence</th>
                        <th>Kickoff</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {leagueGroup.outcomes.map((o, i) => {
                        const picked = pickedFor(o.match_id);
                        const isThisOne = picked?.market === o.market && picked?.selection === o.selection;
                        return (
                          <tr
                            key={`${o.match_id}-${o.market}-${o.selection}-${i}`}
                            onClick={() => navigate(`/app/match/${o.match_id}`)}
                            title={o.definition}
                          >
                            <td>
                              <div className="match-cell">
                                {o.home_team} vs {o.away_team}
                              </div>
                            </td>
                            <td className="sub">{o.market}</td>
                            <td>
                              <strong>{o.selection}</strong>
                            </td>
                            <td className="tabular-nums">{(o.probability * 100).toFixed(0)}%</td>
                            <td>
                              <ConfidenceTag confidence={o.confidence} />
                            </td>
                            <td className="sub">
                              {new Date(o.kickoff).toLocaleString(undefined, {
                                month: "short",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </td>
                            <td style={{ width: 90 }}>
                              <button
                                type="button"
                                className="btn ghost"
                                style={{ padding: "2px 8px", fontSize: 12 }}
                                title={
                                  picked && !isThisOne
                                    ? `Replaces your ${picked.market}: ${picked.selection} pick for this match`
                                    : undefined
                                }
                                onClick={(e) => {
                                  e.stopPropagation();
                                  togglePick(o);
                                }}
                              >
                                {isThisOne ? "✓ Added" : "+ Add"}
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
        </>
      )}

      <p className="setting-note" style={{ marginTop: 4 }}>
        The highlighted box in each row is the model's own selection for that market -- every number is
        its probability, not a bookmaker's price. A high probability is not the same as a good bet, and
        outcomes from different markets can all happen in the same match — stacking them multiplies the
        risk, it doesn't add the confidence.
      </p>
    </div>
  );
}
