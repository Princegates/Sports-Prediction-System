import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { bookPicks, createAdminPick, fetchBranding, fetchMatches, fetchOutcomes } from "../api";
import { ConfidenceTag } from "../components/MostLikelyOutcome";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { MarketPicker, type MarketPreset } from "../components/MarketPicker";
import { SiteCodes } from "../components/SiteCodes";
import { dateKeyFromIso, FixtureCalendar } from "../components/FixtureCalendar";
import { useLeague } from "../components/AppShell";
import { formatPicksForCopy, readStoredPicks, storePicks } from "../lib/myPicks";
import { useAuth } from "../lib/AuthContext";
import type { BettingOutcome, MarketSummary, MatchSummary, OutcomesResponse } from "../types";

/**
 * Every available betting outcome, grouped by league -- laid out as a
 * bookmaker coupon: one row per match, the model's probability for each
 * selection of each chosen market sat in columns. The markets come from one
 * dropdown listing every market the system prices, any number ticked at
 * once (Match Result + GG/NG + Over/Under 2.5, say), each getting its own
 * block of columns. The predictions table answers "what does the system say
 * about this match"; this answers "where is the best Over 2.5 this week,
 * which BTTS calls stand out" -- comparing outcomes across matches, which a
 * per-match view can't do.
 *
 * The coupon asks for exactly the ticked markets in one `fetchOutcomes` call
 * (a repeated `market` parameter), with the per-league cap applied to each
 * market separately -- a match's near-certain exotic outcomes (a 99% Under
 * 7.5) would otherwise crowd other markets out of a shared cap. The list
 * view keeps the original flat, probability-sorted table with its
 * probability-floor and confidence filters, for the same ticked markets or
 * every market when none are.
 *
 * The 3/7/14-day chips are a rolling window from today -- fine for "what's
 * coming up", useless for "what's on the 14th". FixtureCalendar answers
 * that: it's seeded from every SCHEDULED match this league has (no
 * days-ahead cap), so it can show fixture density a season out, and
 * picking a date there computes just enough days_ahead to reach it and
 * filters both views down to that one day.
 */

const DAY_OPTIONS = [3, 7, 14];
const FLOORS = [
  { value: 0, label: "Any" },
  { value: 0.5, label: "50%+" },
  { value: 0.7, label: "70%+" },
  { value: 0.85, label: "85%+" },
];

type MarketView = "coupon" | "list";

// Exported so Settings.tsx's "Default markets" picker (default_market_tab)
// shows the exact same keys and labels this page starts from -- one list,
// never two that could drift apart. The keys predate the dropdown (they
// were tab names) and stay as they are so saved settings keep working.
export type MarketPresetKey = "match_result" | "double_chance" | "btts" | "draw_no_bet" | "other";

export const MARKET_PRESETS: { key: MarketPresetKey; label: string; markets: string[]; view: MarketView }[] = [
  { key: "match_result", label: "Match Result & Goals", markets: ["Match Result", "Total Goals 2.5"], view: "coupon" },
  { key: "double_chance", label: "Double Chance", markets: ["Double Chance"], view: "coupon" },
  { key: "btts", label: "GG/NG", markets: ["Both Teams To Score"], view: "coupon" },
  { key: "draw_no_bet", label: "Draw No Bet", markets: ["Draw No Bet"], view: "coupon" },
  { key: "other", label: "Every market, as a list", markets: [], view: "list" },
];

const QUICK_PICKS: MarketPreset[] = [
  ...MARKET_PRESETS.filter((p) => p.markets.length > 0),
  { label: "1X2 + GG/NG + O/U 2.5", markets: ["Match Result", "Both Teams To Score", "Total Goals 2.5"] },
  { label: "Goal lines", markets: ["Total Goals 1.5", "Total Goals 2.5", "Total Goals 3.5"] },
  { label: "Half-time", markets: ["HT Result", "HT Total Goals 0.5", "HT Total Goals 1.5"] },
];

const SELECTION_STORAGE_KEY = "market_selection";

/** This browser's last dropdown choice -- a convenience only, so it's
 * fine for it to come back empty (private browsing, cleared storage). */
function readStoredSelection(): { markets: string[]; view: MarketView } | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(SELECTION_STORAGE_KEY) ?? "null");
    if (!parsed || !Array.isArray(parsed.markets)) return null;
    if (parsed.view !== "coupon" && parsed.view !== "list") return null;
    return { markets: parsed.markets.filter((m: unknown) => typeof m === "string"), view: parsed.view };
  } catch {
    return null;
  }
}

function storeSelection(markets: string[], view: MarketView): void {
  try {
    localStorage.setItem(SELECTION_STORAGE_KEY, JSON.stringify({ markets, view }));
  } catch {
    // storage disabled -- the choice just won't be remembered
  }
}

interface GridColumn {
  market: string;
  selection: string;
  header: string;
}

interface GridMarket {
  market: string;
  columns: GridColumn[];
}

const RESULT_SHORT: Record<string, string> = { "Home Win": "1", Home: "1", Draw: "X", "Away Win": "2", Away: "2" };
const DOUBLE_CHANCE_SHORT: Record<string, string> = { "Home/Draw": "1X", "Home/Away": "12", "Draw/Away": "X2" };

/** A column header short enough for a coupon -- 1/X/2 and 1X/12/X2 the way
 * bookmakers print them, and "Over"/"Under" rather than repeating the line
 * the market block's own header already shows. */
function shortHeader(market: string, selection: string): string {
  if (market === "Match Result" || market === "HT Result") return RESULT_SHORT[selection] ?? selection;
  if (market === "Double Chance" || market === "HT Double Chance") return DOUBLE_CHANCE_SHORT[selection] ?? selection;
  const line = market.match(/ (\d+\.5)$/)?.[1];
  if (line && selection.endsWith(` ${line}`)) return selection.slice(0, -(line.length + 1));
  return selection;
}

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

  const [stored] = useState(readStoredSelection);
  const [selectedMarkets, setSelectedMarkets] = useState<string[]>(stored?.markets ?? MARKET_PRESETS[0].markets);
  const [view, setView] = useState<MarketView>(stored?.view ?? MARKET_PRESETS[0].view);
  const [days, setDays] = useState(7);
  const [picks, setPicks] = useState<BettingOutcome[]>(readStoredPicks);
  const [featuring, setFeaturing] = useState(false);
  // A choice this browser already made outranks the site-wide default.
  const selectionTouchedByUser = useRef(stored !== null);

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
  // markets), applied once -- and only if this visitor hasn't already made
  // their own choice, now or on an earlier visit.
  useEffect(() => {
    fetchBranding()
      .then((b) => {
        if (selectionTouchedByUser.current) return;
        const preset = MARKET_PRESETS.find((p) => p.key === b.default_market_tab);
        if (preset) {
          setSelectedMarkets(preset.markets);
          setView(preset.view);
        }
      })
      .catch(() => {});
  }, []);

  function chooseMarkets(next: string[]) {
    selectionTouchedByUser.current = true;
    setSelectedMarkets(next);
    storeSelection(next, view);
  }

  function chooseView(next: MarketView) {
    selectionTouchedByUser.current = true;
    setView(next);
    storeSelection(selectedMarkets, next);
  }

  // Every market the system prices for upcoming matches, for the dropdown --
  // one call with a tiny per-league cap, since only its `markets` summary
  // is used. Fixed to the widest window so the list doesn't shrink when a
  // short date range happens to be quiet.
  const [catalog, setCatalog] = useState<MarketSummary[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchOutcomes({ league: league || undefined, days_ahead: 21, limit_per_league: 1 })
      .then((d) => !cancelled && setCatalog(d.markets))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [league]);

  // A remembered or default market this league has no prices for right now
  // still gets a row in the dropdown, so it can be unticked.
  const pickerMarkets = useMemo((): MarketSummary[] => {
    const known = new Set(catalog.map((m) => m.market));
    const missing = selectedMarkets
      .filter((m) => !known.has(m))
      .map((market) => ({ market, group: "", selections: [], outcomes: 0, mutually_exclusive: true }));
    return [...catalog, ...missing];
  }, [catalog, selectedMarkets]);

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

  // -- Coupon view: every ticked market in one outcomes fetch -------------

  const [gridData, setGridData] = useState<OutcomesResponse | null>(null);
  const [gridError, setGridError] = useState<string | null>(null);

  useEffect(() => {
    if (view !== "coupon" || selectedMarkets.length === 0) return;
    let cancelled = false;
    setGridData(null);
    setGridError(null);
    fetchOutcomes({
      market: selectedMarkets, league: league || undefined, days_ahead: effectiveDays,
      min_probability: 0, limit_per_league: 500,
    })
      .then((d) => !cancelled && setGridData(d))
      .catch((e) => !cancelled && setGridError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, [view, selectedMarkets, league, effectiveDays]);

  // One block of columns per ticked market, in the dropdown's order, each
  // with its selections in the order the model lists them.
  const gridMarkets: GridMarket[] = useMemo(() => {
    if (!gridData) return [];
    const byName = new Map(gridData.markets.map((m) => [m.market, m]));
    return selectedMarkets
      .map((name) => byName.get(name))
      .filter((m): m is MarketSummary => !!m)
      .map((m) => ({
        market: m.market,
        columns: m.selections.map((selection) => ({ market: m.market, selection, header: shortHeader(m.market, selection) })),
      }));
  }, [gridData, selectedMarkets]);

  // Each market's columns compete for the "AI selection" highlight among
  // themselves only -- Match Result's pick and Over/Under's pick both show.
  const highlightGroups: string[][] = useMemo(
    () => gridMarkets.map((g) => g.columns.map((c) => cellKey(c.market, c.selection))),
    [gridMarkets],
  );

  const rowsByLeague = useMemo(() => {
    if (!gridData) return [];
    const allOutcomes = gridData.leagues.flatMap((l) => l.outcomes);
    let rows = buildMatchRows([allOutcomes]);
    if (selectedDate) rows = rows.filter((r) => dateKeyFromIso(r.kickoff) === selectedDate);
    const byLeague = groupBy(rows, (r) => r.league).sort(([a], [b]) => a.localeCompare(b));
    return byLeague.map(([leagueName, leagueRows]) => {
      leagueRows.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
      return { league: leagueName, dates: groupBy(leagueRows, (r) => formatDateHeading(r.kickoff)) };
    });
  }, [gridData, selectedDate]);

  // -- List view: the original flat table, sorted by probability ----------

  const [otherFloor, setOtherFloor] = useState(0);
  const [otherConfidence, setOtherConfidence] = useState("");
  const [otherData, setOtherData] = useState<OutcomesResponse | null>(null);
  const [otherError, setOtherError] = useState<string | null>(null);

  useEffect(() => {
    if (view !== "list") return;
    let cancelled = false;
    setOtherData(null);
    setOtherError(null);
    fetchOutcomes({
      league: league || undefined, market: selectedMarkets.length ? selectedMarkets : undefined,
      days_ahead: effectiveDays, min_probability: otherFloor, confidence: otherConfidence || undefined,
    })
      .then((d) => !cancelled && setOtherData(d))
      .catch((e) => !cancelled && setOtherError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, [view, league, selectedMarkets, effectiveDays, otherFloor, otherConfidence]);

  const singleMarket =
    selectedMarkets.length === 1 ? catalog.find((m) => m.market === selectedMarkets[0]) : undefined;

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
        <MarketPicker
          markets={pickerMarkets}
          selected={selectedMarkets}
          onChange={chooseMarkets}
          presets={QUICK_PICKS}
          emptyLabel={view === "list" ? "All markets" : "Choose markets"}
        />
        <button
          type="button"
          className={`filter-chip${view === "coupon" ? " active" : ""}`}
          onClick={() => chooseView("coupon")}
        >
          Coupon
        </button>
        <button
          type="button"
          className={`filter-chip${view === "list" ? " active" : ""}`}
          onClick={() => chooseView("list")}
        >
          List by probability
        </button>
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
            Picked while browsing -- the percentages are the model's probability, not bookmaker odds.
            Get a booking code for these picks below: the betting site builds the slip and sets its own
            odds, which you'll see when you open it there. Or copy them and add them yourself.
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

          <SiteCodes
            legs={picks}
            book={(sites) =>
              bookPicks(
                picks.map((p) => ({ match_id: p.match_id, market: p.market, selection: p.selection })),
                sites,
              )
            }
          />
        </div>
      )}

      {view === "coupon" ? (
        <>
          {selectedMarkets.length === 0 && (
            <EmptyState icon="◌" title="Pick one or more markets from the dropdown above." />
          )}
          {selectedMarkets.length > 0 && gridError && <ErrorState message={gridError} />}
          {selectedMarkets.length > 0 && !gridError && !gridData && <p className="badge-neutral">Loading outcomes…</p>}
          {selectedMarkets.length > 0 && !gridError && gridData && rowsByLeague.length === 0 && (
            <EmptyState
              icon="◌"
              title={
                selectedDate
                  ? `No scheduled matches with a prediction on ${formatDateHeading(`${selectedDate}T00:00:00`)}.`
                  : "No scheduled matches with a prediction in this window."
              }
            />
          )}

          {selectedMarkets.length > 0 &&
            !gridError &&
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
                    <p className="scroll-hint">Swipe to see every market →</p>
                    <div className="predictions-table-wrapper">
                      <table className="predictions-table odds-grid">
                        <thead>
                          <tr>
                            <th rowSpan={2} className="match-col">
                              Match
                            </th>
                            {gridMarkets.map((g) => (
                              <th key={g.market} colSpan={g.columns.length} className="market-head">
                                {g.market}
                              </th>
                            ))}
                          </tr>
                          <tr>
                            {gridMarkets.flatMap((g) =>
                              g.columns.map((c, i) => (
                                <th
                                  key={cellKey(c.market, c.selection)}
                                  className={`selection-head tabular-nums${i === 0 ? " market-start" : ""}`}
                                  title={c.selection}
                                >
                                  {c.header}
                                </th>
                              )),
                            )}
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((row) => {
                            const tops = new Set(highlightGroups.map((g) => topKeyIn(row, g)).filter(Boolean) as string[]);
                            const picked = pickedFor(row.match_id);
                            return (
                              <tr key={row.match_id}>
                                <td className="match-col">
                                  <div className="match-cell" style={{ cursor: "pointer" }} onClick={() => navigate(`/app/match/${row.match_id}`)}>
                                    {row.home_team} vs {row.away_team}
                                  </div>
                                  <div className="sub">{formatTime(row.kickoff)}</div>
                                </td>
                                {gridMarkets.flatMap((g) =>
                                  g.columns.map((c, i) => {
                                    const key = cellKey(c.market, c.selection);
                                    const o = row.cells.get(key);
                                    const isPicked = !!o && picked?.market === o.market && picked?.selection === o.selection;
                                    return (
                                      <td key={key} className={i === 0 ? "market-start" : undefined} style={{ textAlign: "center" }}>
                                        {o ? (
                                          <button
                                            type="button"
                                            className={`odds-cell${tops.has(key) ? " ai-top" : ""}${isPicked ? " picked" : ""}`}
                                            onClick={() => togglePick(o)}
                                            title={`${o.market}: ${o.selection} -- ${o.definition} AI probability ${(o.probability * 100).toFixed(0)}%`}
                                          >
                                            {(o.probability * 100).toFixed(0)}%
                                          </button>
                                        ) : (
                                          <span className="odds-cell empty">—</span>
                                        )}
                                      </td>
                                    );
                                  }),
                                )}
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

          {singleMarket && (
            <p className="setting-note" style={{ marginBottom: 18 }}>
              <strong>{singleMarket.market}</strong> — {singleMarket.selections.join(" · ")}.{" "}
              {singleMarket.mutually_exclusive
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

                <p className="scroll-hint">Swipe to see every column →</p>
                <div className="predictions-table-wrapper">
                  <table className="predictions-table wide">
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
