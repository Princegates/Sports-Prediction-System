import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  clearPublicTeaserPick,
  createAdminPick,
  deleteAdminPick,
  fetchAdminPicksAdmin,
  fetchMatchesForLeagues,
  fetchOutcomesForLeagues,
  fetchPublicTeaserPickAdmin,
  bookPicks,
  promotePickToPublicTeaser,
  resolvePicks,
  setAdminPickFeaturedPublic,
  setAdminPickResult,
  suggestPicks,
  suggestRandomPicks,
  updateAdminPick,
} from "../api";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { FixtureCalendar } from "../components/FixtureCalendar";
import { LeaguePicker } from "../components/LeaguePicker";
import { MarketPicker, type MarketPreset } from "../components/MarketPicker";
import { MyCodesHistory } from "../components/MyCodesHistory";
import { SiteCodes } from "../components/SiteCodes";
import { StickySlipBar } from "../components/StickySlipBar";
import { LEAGUES, useLeague } from "../components/AppShell";
import { useAuth } from "../lib/AuthContext";
import { useIsMobile } from "../lib/useIsMobile";
import { takePendingSlip } from "../lib/pendingSlip";
import type { AdminPick, BetCodePick, MarketSummary, MatchSummary, PublicTeaserPick, SuggestCriteria, SuggestedLeg, SuggestedPicks } from "../types";
import { chanceLabel } from "../lib/odds";
import { marketLabel } from "../lib/marketLabel";

/** Plain-text description of one selected combo, meant to be pasted
 * wherever the user places bets themselves. No bookmaker code, no odds
 * figure -- nothing here is priced, see this file's module docstring. */
function formatLegsForCopy(legs: SuggestedLeg[], combinedProbability: number): string {
  const header = `AI Generation -- ${legs.length} leg combo, ${(combinedProbability * 100).toFixed(0)}% combined probability (copied ${new Date().toLocaleString()})`;
  const lines = legs.map((leg) => {
    const kickoff = new Date(leg.kickoff).toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    return (
      `${leg.home_team} vs ${leg.away_team} (${leg.league}, ${kickoff})\n` +
      `${marketLabel(leg.market)}: ${leg.selection} -- ${(leg.model_probability * 100).toFixed(0)}% probability`
    );
  });
  return [header, "", ...lines].join("\n\n");
}

/**
 * The "AI Generation" step: picks a multi-match selection by model
 * confidence alone -- no bookmaker price anywhere in this file. Pick a
 * betting site under the legs and it books the slip (and prices it) at its
 * own odds; nothing here ever estimates or invents a price of its own.
 */

// The engine's own default when nothing is explicitly picked -- matches
// the backend's AI_PICKS_LEAGUES (app.betcode.selection) exactly. No
// longer a ceiling: the league picker below (LeaguePicker, same component
// Markets/Predictions use) offers the app's full LEAGUES list, and an
// explicit pick from it is searched as asked, same reasoning as
// MARKET_OPTIONS -> the full market catalog above. Kept here as a quick
// "Big 8" preset in that picker, and as the fallback label/behavior for
// "nothing selected".
const AI_PICKS_LEAGUES = [
  "English Premier League",
  "Spanish La Liga",
  "German Bundesliga",
  "Italian Serie A",
  "French Ligue 1",
  "Dutch Eredivisie",
  "Portuguese Primeira Liga",
  "Turkish Süper Lig",
];

// The engine's own safe default when nothing is explicitly ticked below --
// matches the backend's DEFAULT_MARKETS (app.betcode.selection) exactly.
// Shown as a quick preset, not the ceiling: the picker itself (below) now
// offers the full catalog the outcome registry prices, same as the Markets
// page's own dropdown, grouped and searchable rather than two flat chip
// rows capped at twelve markets total.
const DEFAULT_MARKET_PRESET: MarketPreset = {
  label: "Default (match result, double chance, draw no bet, O/U 1.5, handicap up to 2 goals)",
  markets: [
    "Match Result", "Double Chance", "Draw No Bet", "Total Goals 1.5",
    "Handicap -1", "Handicap +1", "Handicap -2", "Handicap +2",
  ],
};

const AI_MARKET_PRESETS: MarketPreset[] = [
  DEFAULT_MARKET_PRESET,
  { label: "1X2 + GG/NG + O/U 2.5", markets: ["Match Result", "Both Teams To Score", "Total Goals 2.5"] },
  { label: "Goal lines", markets: ["Total Goals 1.5", "Total Goals 2.5", "Total Goals 3.5"] },
  { label: "Half-time", markets: ["HT Result", "HT Total Goals 0.5", "HT Total Goals 1.5"] },
  { label: "1st & 2nd half goals", markets: ["HT Total Goals 0.5", "HT Total Goals 1.5", "2H Total Goals 0.5", "2H Total Goals 1.5"] },
  { label: "Corners", markets: ["Most Corners", "Total Corners 9.5"] },
];

const AI_LEAGUE_PRESETS = [
  { label: "Big 8", keys: AI_PICKS_LEAGUES },
  { label: "All leagues", keys: LEAGUES },
];

// The FixtureCalendar's own data source (below) used to ask for every
// SCHEDULED match ever imported, no cutoff at all -- harmless for a single
// league, but combined with "All leagues" (every league, one click) that
// query has no bound on either leagues or time and is what ran the backend
// out of its 512MB memory budget. 120 days is generous for a calendar
// someone is browsing month by month to cover (four months of "Next
// month" clicks), while still being a real cutoff.
const CALENDAR_DAYS_AHEAD = 120;

const ACCURACY_OPTIONS = [
  { value: 0.1, label: "10%+" },
  { value: 0.2, label: "20%+" },
  { value: 0.3, label: "30%+" },
  { value: 0.4, label: "40%+" },
  { value: 0.5, label: "50%+" },
  { value: 0.65, label: "65%+" },
  { value: 0.75, label: "75%+" },
  { value: 0.85, label: "85%+" },
];

// Matches the backend's own default (select_legs_by_confidence's own
// max_legs default) so leaving this untouched keeps prior behavior.
const DEFAULT_MAX_LEGS = 8;
const LEG_COUNT_OPTIONS = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30];

type RiskLevel = "low" | "medium" | "high";

/**
 * One-click generation presets. Each fills in (and immediately runs) the
 * same criteria fields the form below exposes -- these aren't a separate
 * mode, just a fast way to reach a sensible corner of the same search.
 *
 * The lever that actually drives risk here: how safe the floor is (more
 * legs isn't a separate lever the way it was when a leg count doubled as
 * "keep searching until the price target is hit" -- here it's just how
 * many of the safest qualifying matches to show). Markets aren't a risk
 * lever either: the presets leave `markets` empty, which the engine takes
 * as its own curated default (DEFAULT_MARKET_PRESET above), except High
 * risk -- every leg uses a different market, and eight legs need more than
 * the default's five. The full catalog is there to explore by hand in the
 * Markets step, not folded into a one-click preset.
 */
const RISK_PRESETS: Record<
  RiskLevel,
  { label: string; description: string; minProbability: number; maxLegs: number; markets: string[] }
> = {
  low: {
    label: "Low risk",
    description: "85%+ picks only, up to 3 legs, mixed markets",
    minProbability: 0.85,
    maxLegs: 3,
    markets: [],
  },
  medium: {
    label: "Medium risk",
    description: "65%+ picks, up to 5 legs",
    minProbability: 0.65,
    maxLegs: 5,
    markets: [],
  },
  high: {
    label: "High risk",
    description: "50%+ picks, up to 8 legs",
    minProbability: 0.5,
    maxLegs: 8,
    // Each leg uses a different market, and the default set only has five
    // (the handicap lines count as one) -- eight legs need eight markets.
    markets: [
      "Match Result", "Double Chance", "Draw No Bet", "Total Goals 1.5",
      "Handicap -1", "Handicap +1", "Handicap -2", "Handicap +2",
      "Both Teams To Score", "Total Goals 2.5", "Total Goals 3.5",
    ],
  },
};

// The custom-criteria card below is one long form -- six sections plus a
// Generate button -- which is fine to scroll through on a desktop screen
// but is a lot of one-handed scrolling on a phone before a first-time user
// even reaches the button. On mobile only (see useIsMobile), it's split
// into these steps with Back/Next controls instead; desktop keeps every
// section visible at once, unchanged. The risk-preset card above stays a
// single always-visible fast path on both -- it isn't part of this form.
const WIZARD_STEPS = ["When & where", "Legs & accuracy", "Markets", "Review & generate"] as const;

// Labels the *result*, independent of which preset (if any) produced it --
// a "High risk" generate that only found 2 very safe legs is honestly a
// low-risk result, and this says so rather than repeating the input choice.
function resultRiskLabel(combinedProbability: number): { label: string; tone: "low" | "medium" | "high" } {
  if (combinedProbability >= 0.5) return { label: "Low risk", tone: "low" };
  if (combinedProbability >= 0.2) return { label: "Medium risk", tone: "medium" };
  return { label: "High risk", tone: "high" };
}

export function BetCodes() {
  const { league } = useLeague();
  const { user } = useAuth();
  const location = useLocation();
  const [maxLegs, setMaxLegs] = useState(DEFAULT_MAX_LEGS);
  const [markets, setMarkets] = useState<string[]>([]);
  // Off by default: each match contributes its single strongest qualifying
  // outcome, regardless of which selected market that is. On: every market
  // ticked above gets its own leg, on its own match, whenever the data
  // allows -- for someone who ticked several markets specifically to see
  // all of them on the slip, not just whichever one wins each match's own
  // probability contest. Only meaningful with a non-empty `markets`.
  const [featureAllMarkets, setFeatureAllMarkets] = useState(false);
  const [minProbability, setMinProbability] = useState(0.65);
  const [daysAhead, setDaysAhead] = useState(7);
  // Which leagues to search -- initialized from the top bar's single-league
  // context (so this page starts scoped the same way it always has), but
  // from here on this list, not the top bar, is what actually drives the
  // search: empty means every league this deployment tracks, and any
  // number can be picked at once, not just the 8 AI_PICKS_LEAGUES default.
  const [selectedLeagues, setSelectedLeagues] = useState<string[]>(league ? [league] : []);
  // Which risk preset (if any) exactly matches the form's current values --
  // cleared the moment any control is touched by hand, so the highlighted
  // chip never claims a match that no longer holds.
  const [activeRisk, setActiveRisk] = useState<RiskLevel | null>(null);

  // A calendar pick (below) takes over from the Window select when set --
  // one click's worth of "just this day" instead of a rolling N-day
  // window. calendarMatches is every SCHEDULED match for the leagues in
  // scope within CALENDAR_DAYS_AHEAD -- same source/shape as Markets.tsx's
  // own calendar, just for the dots, never rendered as rows itself.
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [calendarMatches, setCalendarMatches] = useState<MatchSummary[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchMatchesForLeagues(selectedLeagues, { status: "SCHEDULED", days_ahead: CALENDAR_DAYS_AHEAD })
      .then((m) => !cancelled && setCalendarMatches(m))
      .catch(() => !cancelled && setCalendarMatches([]));
    return () => {
      cancelled = true;
    };
  }, [selectedLeagues]);

  // Every market the system currently prices for the leagues in scope, for
  // the Markets step's picker -- same source and shape the Markets page's
  // own dropdown uses (one tiny-cap call, only the `markets` summary is
  // read), scoped to whichever leagues are selected (every league when
  // none are).
  const [marketCatalog, setMarketCatalog] = useState<MarketSummary[]>([]);
  const [marketCatalogLoading, setMarketCatalogLoading] = useState(true);
  // Set only when the catalog fetch itself failed (a cold backend timing
  // out, a dropped connection) -- distinct from a fetch that genuinely
  // succeeded with zero markets, which is a real "nothing priced" state,
  // not an error. Without this the two looked identical: both landed on
  // the same "no markets" empty state with no way to tell a transient
  // failure from an honest empty result, or any way to retry one.
  const [marketCatalogError, setMarketCatalogError] = useState(false);
  // Bumped by the retry button below to re-run the effect without also
  // depending on `selectedLeagues` changing.
  const [marketCatalogRetryTick, setMarketCatalogRetryTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setMarketCatalogLoading(true);
    setMarketCatalogError(false);
    fetchOutcomesForLeagues(selectedLeagues, { days_ahead: 21, limit_per_league: 1 })
      .then((d) => !cancelled && setMarketCatalog(d.markets))
      .catch(() => !cancelled && setMarketCatalogError(true))
      .finally(() => !cancelled && setMarketCatalogLoading(false));
    return () => {
      cancelled = true;
    };
  }, [selectedLeagues, marketCatalogRetryTick]);

  const isMobile = useIsMobile();
  const [wizardStep, setWizardStep] = useState(0);
  // Desktop shows every section regardless of step, so the value doesn't
  // matter there -- guarded to a valid index anyway in case a resize lands
  // mid-session on a step a previous (wider or narrower) layout allowed.
  const step = Math.min(wizardStep, WIZARD_STEPS.length - 1);

  const [preview, setPreview] = useState<SuggestedPicks | null>(null);
  const resultsCardRef = useRef<HTMLDivElement | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  // Set only when the current preview came from picks handed over by Guda
  // (or another page) rather than the criteria form below -- changes the
  // result card's framing, not how it's resolved.
  const [fromExternalPicks, setFromExternalPicks] = useState(false);
  // Which search produced the current preview -- activeRisk alone can't
  // tell a custom-criteria run apart from a random draw, since both leave
  // it null (every form control already clears it on touch). Refresh reads
  // this, alongside activeRisk, to re-run the exact same search rather
  // than guessing which one is active.
  const [lastRunWasRandom, setLastRunWasRandom] = useState(false);
  // Every match_id the visible preview already has a leg from, across
  // however many times Refresh has been clicked in a row -- sent back as
  // exclude_match_ids so a Refresh search genuinely explores the rest of
  // the candidate pool instead of landing on the identical, deterministic
  // result safest-first search always gives for unchanged criteria over
  // unchanged data. A fresh Generate/preset/Random run (not a Refresh)
  // starts this over rather than adding to it -- see runPreview.
  const [excludedMatchIds, setExcludedMatchIds] = useState<number[]>([]);
  // Legs manually removed from the current preview, keyed the same way a
  // leg is (match_id|market|selection) -- cleared to empty every time
  // `preview` itself is replaced (see runPreview and handleEditPick, the
  // only two places that ever call setPreview). Removal is a client-side
  // filter, not a fresh search: the combined probability below is
  // recomputed by simply dropping that leg's own contribution.
  const [removedLegKeys, setRemovedLegKeys] = useState<Set<string>>(new Set());

  // Superadmin-only: promoting the current preview as an "Admin Pick" onto
  // every Dashboard (distinct from Guda Picks' single-outcome promotion).
  const [adminPicks, setAdminPicks] = useState<AdminPick[]>([]);
  const [featuring, setFeaturing] = useState(false);
  const [pickBusy, setPickBusy] = useState<number | null>(null);
  // Set while editing an already-featured Admin Pick (via its "Edit" button
  // below) rather than building a brand-new one -- changes what the
  // Feature/Save button does and label/note prompts pre-fill with.
  const [editingPickId, setEditingPickId] = useState<number | null>(null);

  // Superadmin-only: the one showcase combo shown with real selections on
  // the public, unauthenticated Welcome page -- see PublicTeaserPick's own
  // docstring for why this is a deliberate exception.
  const [teaserPick, setTeaserPick] = useState<PublicTeaserPick | null>(null);
  const [teaserBusy, setTeaserBusy] = useState(false);
  // Set right after a successful promote so the (easy-to-miss, below-the-
  // fold) "Public teaser pick" card can call out that the click actually
  // took effect -- a promote has no other feedback of its own otherwise.
  const [teaserJustPromoted, setTeaserJustPromoted] = useState(false);
  const teaserCardRef = useRef<HTMLDivElement | null>(null);

  function legKey(leg: { match_id: number; market: string; selection: string }): string {
    return `${leg.match_id}|${leg.market}|${leg.selection}`;
  }

  const editedLegs = preview ? preview.legs.filter((l) => !removedLegKeys.has(legKey(l))) : [];
  const editedCombinedProbability = editedLegs.reduce((acc, l) => acc * l.model_probability, 1);

  useEffect(() => {
    if (user?.role !== "superadmin") return;
    let cancelled = false;
    fetchAdminPicksAdmin()
      .then((picks) => !cancelled && setAdminPicks(picks))
      .catch(() => {});
    fetchPublicTeaserPickAdmin()
      .then((pick) => !cancelled && setTeaserPick(pick))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user?.role]);

  async function handleClearTeaser() {
    setTeaserBusy(true);
    try {
      await clearPublicTeaserPick();
      setTeaserPick({ has_data: false, legs: [], combined_odds: 0, combined_probability: 0, label: null, created_at: null });
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setTeaserBusy(false);
    }
  }

  function criteria(overrides?: Partial<SuggestCriteria>): SuggestCriteria {
    return {
      max_legs: maxLegs,
      markets,
      min_probability: minProbability,
      leagues: selectedLeagues,
      days_ahead: daysAhead,
      date_from: selectedDate,
      date_to: selectedDate,
      feature_all_markets: featureAllMarkets,
      ...overrides,
    };
  }

  // `resetExclusions` true for a fresh Generate/preset/Random run (starts
  // excludedMatchIds over from just this result), false for a Refresh
  // (adds this result's matches on top of whatever was already excluded,
  // so the next Refresh keeps exploring forward instead of circling back).
  async function runPreview(fetchPreview: () => Promise<SuggestedPicks>, resetExclusions: boolean) {
    setPreviewing(true);
    setPreviewError(null);
    setEditingPickId(null); // a fresh search is never mid-edit of an existing pick
    try {
      const result = await fetchPreview();
      setPreview(result);
      setRemovedLegKeys(new Set());
      const newIds = result.legs.map((l) => l.match_id);
      setExcludedMatchIds((prev) => (resetExclusions ? newIds : [...new Set([...prev, ...newIds])]));
    } catch (e) {
      setPreview(null);
      setPreviewError(String(e instanceof Error ? e.message : e));
    } finally {
      setPreviewing(false);
    }
  }

  // A pick list handed over via navigation (Guda's "Send to AI Generation",
  // Markets' "My picks" panel, the public free picks, or any future source) resolves immediately
  // on arrival -- once per navigation, not on every re-render.
  useEffect(() => {
    // ...or the public free-picks slip a visitor asked to book before they
    // had an account (lib/pendingSlip), the first time they get here.
    const pending = takePendingSlip();
    const picks = (location.state as { picks?: BetCodePick[] } | null)?.picks ?? pending;
    if (!picks || picks.length === 0) return;
    setFromExternalPicks(true);
    runPreview(() => resolvePicks(picks), true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

  // `exclude` is only ever non-empty when Refresh calls this -- a plain
  // click (every other call site) passes none, which is also what decides
  // runPreview's own reset-vs-accumulate behavior below.
  function handlePreview(exclude: number[] = []) {
    setFromExternalPicks(false);
    setLastRunWasRandom(false);
    return runPreview(() => suggestPicks(criteria({ exclude_match_ids: exclude })), exclude.length === 0);
  }

  // Fills in the preset's values (so the form reflects what actually ran,
  // and stays tweakable afterward) and generates immediately -- one click
  // for "just get me a low-risk slip" rather than four. `exclude`: see
  // handlePreview above.
  function runPreset(risk: RiskLevel, exclude: number[] = []) {
    const p = RISK_PRESETS[risk];
    setMaxLegs(p.maxLegs);
    setMinProbability(p.minProbability);
    setMarkets(p.markets);
    setFeatureAllMarkets(false);
    setActiveRisk(risk);
    setFromExternalPicks(false);
    setLastRunWasRandom(false);
    return runPreview(
      () =>
        suggestPicks(
          criteria({
            max_legs: p.maxLegs, min_probability: p.minProbability, markets: p.markets,
            feature_all_markets: false, exclude_match_ids: exclude,
          }),
        ),
      exclude.length === 0,
    );
  }

  // Not a risk preset -- a random 10-20 leg draw from the same candidate
  // pool, in place of the form's confidence-ranked search entirely.
  // `exclude`: see handlePreview above.
  function runRandomSlip(exclude: number[] = []) {
    setActiveRisk(null);
    setFromExternalPicks(false);
    setLastRunWasRandom(true);
    return runPreview(
      () =>
        suggestRandomPicks({
          leagues: selectedLeagues, days_ahead: daysAhead, date_from: selectedDate, date_to: selectedDate,
          exclude_match_ids: exclude,
        }),
      exclude.length === 0,
    );
  }

  // Re-runs whichever search produced the current preview, with the same
  // criteria still sitting in the form, excluding every match already
  // shown so far this refresh chain -- a random draw already varies on its
  // own and gets this as a further guarantee; a risk preset or the
  // custom-criteria form would otherwise come back with the identical
  // result every time, since safest-first search is deterministic over
  // unchanged data.
  function handleRefresh() {
    if (activeRisk) return runPreset(activeRisk, excludedMatchIds);
    if (lastRunWasRandom) return runRandomSlip(excludedMatchIds);
    return handlePreview(excludedMatchIds);
  }

  // Drops one leg from the current preview -- client-side only, no new
  // search. The result card's combined probability and leg table reflect
  // editedLegs immediately; nothing is saved until Feature/Save is clicked
  // below.
  function removeLeg(leg: SuggestedLeg) {
    setRemovedLegKeys((prev) => new Set(prev).add(legKey(leg)));
  }

  // Loads an already-featured (unpriced) Admin Pick's legs into the
  // results card as if they'd just been generated, so the same remove-a-leg
  // UI above can edit it. Feature this slip becomes Save changes for as
  // long as this stays set. A legacy priced pick (from before this page
  // dropped odds) isn't editable here -- see the Edit button's own guard.
  function handleEditPick(pick: AdminPick) {
    if (pick.priced) return;
    setPreview({
      legs: pick.legs.map((l) => ({
        match_id: l.match_id, league: l.league, home_team: l.home_team, away_team: l.away_team,
        kickoff: l.kickoff, market: l.market, selection: l.selection, model_probability: l.probability,
      })),
      combined_probability: pick.combined_probability,
      candidates_considered: pick.legs.length,
      warnings: [],
    });
    setRemovedLegKeys(new Set());
    setFromExternalPicks(false);
    setPreviewError(null);
    setEditingPickId(pick.id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function handleCancelEdit() {
    setEditingPickId(null);
    setPreview(null);
  }

  // Promotes the current (possibly leg-edited) preview onto every
  // Dashboard's Admin Picks section, or -- while editing an existing one --
  // saves those same edits back onto it in place. Always unpriced: nothing
  // on this page is priced anymore, so every leg is resolved by model
  // probability alone (same as Markets' "My picks" panel), never re-priced
  // from a bookmaker quote.
  async function handleFeatureOrSave() {
    if (editedLegs.length === 0) return;
    const editing = editingPickId !== null;
    const currentPick = editing ? adminPicks.find((p) => p.id === editingPickId) : undefined;

    const label = window.prompt(
      editing
        ? "Label for this slip -- Cancel to stop editing without saving:"
        : "Optional label for this slip (e.g. \"Weekend Banker\") -- Cancel to skip featuring it:",
      currentPick?.label ?? "",
    );
    if (label === null) return;
    const note = window.prompt("Optional note (shown on every Dashboard):", currentPick?.note ?? "") ?? undefined;

    // Never generated by this platform -- whatever the admin typed after
    // building the identical slip by hand on a real bookmaker's site.
    // Shown only to premium viewers once saved (see AdminPickCard). Leaving
    // the bookmaker blank -- including clearing a pre-filled one while
    // editing -- drops any code entirely rather than sending a mismatched
    // pair the backend would reject anyway.
    let bookingCode: string | undefined;
    let bookingCodeBookmaker: string | undefined;
    const bookmakerInput = window.prompt(
      "Bookmaker this booking code is for (optional -- leave blank to skip):",
      currentPick?.booking_code_bookmaker ?? "",
    );
    if (bookmakerInput) {
      const codeInput = window.prompt(`Booking code from ${bookmakerInput}:`, currentPick?.booking_code ?? "");
      if (codeInput) {
        bookingCodeBookmaker = bookmakerInput;
        bookingCode = codeInput;
      }
    }

    const legsPayload = editedLegs.map((l) => ({ match_id: l.match_id, market: l.market, selection: l.selection }));
    setFeaturing(true);
    try {
      const payload = {
        legs: legsPayload,
        label: label || undefined,
        note: note || undefined,
        priced: false,
        booking_code: bookingCode,
        booking_code_bookmaker: bookingCodeBookmaker,
      };
      if (editing && editingPickId !== null) {
        const updated = await updateAdminPick(editingPickId, payload);
        setAdminPicks((prev) => prev.map((p) => (p.id === editingPickId ? updated : p)));
        setEditingPickId(null);
      } else {
        const created = await createAdminPick(payload);
        setAdminPicks((prev) => [created, ...prev]);
      }
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setFeaturing(false);
    }
  }

  async function handleRemoveAdminPick(pickId: number) {
    setPickBusy(pickId);
    try {
      await deleteAdminPick(pickId);
      setAdminPicks((prev) => prev.filter((p) => p.id !== pickId));
      if (editingPickId === pickId) handleCancelEdit();
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPickBusy(null);
    }
  }

  async function handleSetPickResult(pickId: number, result: "pending" | "won" | "lost" | "unresolved") {
    setPickBusy(pickId);
    try {
      const updated = await setAdminPickResult(pickId, result);
      setAdminPicks((prev) => prev.map((p) => (p.id === pickId ? updated : p)));
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPickBusy(null);
    }
  }

  async function handleToggleFeaturedPublic(pickId: number, featuredPublic: boolean) {
    setPickBusy(pickId);
    try {
      const updated = await setAdminPickFeaturedPublic(pickId, featuredPublic);
      setAdminPicks((prev) => prev.map((p) => (p.id === pickId ? updated : p)));
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPickBusy(null);
    }
  }

  async function handlePromoteToTeaser(pickId: number) {
    setPickBusy(pickId);
    try {
      const updated = await promotePickToPublicTeaser(pickId);
      setTeaserPick(updated);
      setTeaserJustPromoted(true);
      teaserCardRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => setTeaserJustPromoted(false), 4000);
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPickBusy(null);
    }
  }

  return (
    <div className={editedLegs.length > 0 ? "has-sticky-slip-space" : undefined}>
      <div className="section-header">
        <h2>AI Generation</h2>
        <span className="meta">
          Combine matches by model confidence -- scoped to{" "}
          <strong>
            {selectedLeagues.length === 0
              ? "the Big 8"
              : selectedLeagues.length === 1
                ? selectedLeagues[0]
                : `${selectedLeagues.length} leagues`}
          </strong>{" "}
          (pick leagues below)
        </span>
      </div>

      <p className="setting-note" style={{ marginBottom: 16 }}>
        Every leg here is model probability, not a bookmaker price -- combining matches multiplies the
        risk exactly as fast as it would multiply a price: three legs each 70% likely land around a
        34% chance of all three coming in. Nothing here is priced or estimated. Pick a betting site
        under the legs and it books the slip (and prices it) at its own odds, or copy the selections
        and add them yourself.
      </p>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div className="meta" style={{ marginBottom: 10 }}>
          Generate by risk level
        </div>
        <div className="risk-preset-row">
          {(Object.keys(RISK_PRESETS) as RiskLevel[]).map((risk) => {
            const p = RISK_PRESETS[risk];
            return (
              <button
                key={risk}
                type="button"
                className={`risk-preset-card ${risk}${activeRisk === risk ? " active" : ""}`}
                onClick={() => runPreset(risk)}
                disabled={previewing}
              >
                <span className="risk-preset-label">{p.label}</span>
                <span className="risk-preset-description">{p.description}</span>
              </button>
            );
          })}
          <button
            type="button"
            className="risk-preset-card random"
            onClick={() => runRandomSlip()}
            disabled={previewing}
          >
            <span className="risk-preset-label">Random slip</span>
            <span className="risk-preset-description">10-20 legs, drawn at random -- not ranked by risk level</span>
          </button>
        </div>
        <p className="setting-note" style={{ marginTop: 10, marginBottom: 0 }}>
          Fills in (and runs) the fields below -- feel free to adjust anything afterward and generate again. Every
          leg uses a different market. Random slip generates straight away and ignores the fields below except
          league and date range.
        </p>
      </div>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        {isMobile && (
          <div className="wizard-progress" style={{ marginBottom: 16 }}>
            <div className="meta" style={{ marginBottom: 6 }}>
              Step {step + 1} of {WIZARD_STEPS.length} — {WIZARD_STEPS[step]}
            </div>
            <div className="wizard-progress-track">
              {WIZARD_STEPS.map((label, i) => (
                <span key={label} className={`wizard-progress-dot${i <= step ? " done" : ""}`} />
              ))}
            </div>
          </div>
        )}

        {(!isMobile || step === 0) && (
          <>
            <div className="auth-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
              <label>
                Window{selectedDate ? <span style={{ fontWeight: 400 }}> (overridden by the date below)</span> : null}
                <select
                  value={daysAhead}
                  disabled={!!selectedDate}
                  onChange={(e) => {
                    setActiveRisk(null);
                    setDaysAhead(Number(e.target.value));
                  }}
                >
                  {[3, 7, 14].map((d) => (
                    <option key={d} value={d}>
                      Next {d} days
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div style={{ marginTop: 14 }}>
              <div className="meta" style={{ marginBottom: 6 }}>
                Or pick a specific date <span style={{ fontWeight: 400 }}>(overrides the window above)</span>
              </div>
              {selectedDate ? (
                <div className="filter-bar">
                  <span className="filter-chip active">
                    {new Date(`${selectedDate}T00:00:00`).toLocaleDateString(undefined, {
                      weekday: "short", month: "short", day: "numeric",
                    })}
                  </span>
                  <button
                    type="button"
                    className="filter-chip"
                    onClick={() => {
                      setActiveRisk(null);
                      setSelectedDate(null);
                    }}
                  >
                    Clear date
                  </button>
                </div>
              ) : (
                <>
                  <button type="button" className="filter-chip" onClick={() => setCalendarOpen((o) => !o)}>
                    {calendarOpen ? "Hide calendar" : "Choose a date"}
                  </button>
                  {calendarOpen && (
                    <div style={{ marginTop: 8 }}>
                      <FixtureCalendar
                        matches={calendarMatches}
                        selectedDate={selectedDate}
                        onSelectDate={(d) => {
                          setActiveRisk(null);
                          setSelectedDate(d);
                          setCalendarOpen(false);
                        }}
                      />
                    </div>
                  )}
                </>
              )}
            </div>

            <div style={{ marginTop: 14 }}>
              <div className="meta" style={{ marginBottom: 6 }}>
                Leagues{" "}
                <span style={{ fontWeight: 400 }}>(every league this deployment tracks -- none selected = the usual Big 8)</span>
              </div>
              <LeaguePicker
                leagues={LEAGUES}
                selected={selectedLeagues}
                onChange={(next) => {
                  setActiveRisk(null);
                  setSelectedLeagues(next);
                }}
                presets={AI_LEAGUE_PRESETS}
                emptyLabel="Big 8 (default)"
              />
            </div>
          </>
        )}

        {(!isMobile || step === 1) && (
          <>
            <div style={{ marginTop: isMobile ? 0 : 14 }}>
              <div className="meta" style={{ marginBottom: 6 }}>
                Number of legs
              </div>
              <div className="filter-bar">
                {LEG_COUNT_OPTIONS.map((n) => (
                  <button
                    key={n}
                    className={`filter-chip${maxLegs === n ? " active" : ""}`}
                    onClick={() => {
                      setActiveRisk(null);
                      setMaxLegs(n);
                    }}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>

            <div style={{ marginTop: 14 }}>
              <div className="meta" style={{ marginBottom: 6 }}>
                Minimum accuracy per leg
              </div>
              <div className="filter-bar">
                {ACCURACY_OPTIONS.map((o) => (
                  <button
                    key={o.value}
                    className={`filter-chip${minProbability === o.value ? " active" : ""}`}
                    onClick={() => {
                      setActiveRisk(null);
                      setMinProbability(o.value);
                    }}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        {(!isMobile || step === 2) && (
          <div style={{ marginTop: isMobile ? 0 : 14 }}>
            <div className="meta" style={{ marginBottom: 6 }}>
              Markets{" "}
              <span style={{ fontWeight: 400 }}>
                (every market the system currently prices -- search or browse by group; none selected = the default six)
              </span>
            </div>
            <MarketPicker
              markets={marketCatalog}
              selected={markets}
              onChange={(next) => {
                setActiveRisk(null);
                setMarkets(next);
              }}
              presets={AI_MARKET_PRESETS}
              emptyLabel="Default markets"
              loading={marketCatalogLoading || marketCatalogError}
            />
            {marketCatalogError && !marketCatalogLoading && (
              <p className="setting-note" style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 10 }}>
                Couldn't load the market catalog -- the backend may be waking up from idle.{" "}
                <button
                  type="button"
                  className="btn ghost"
                  style={{ padding: "2px 10px", fontSize: 12.5 }}
                  onClick={() => setMarketCatalogRetryTick((t) => t + 1)}
                >
                  Retry
                </button>
              </p>
            )}
            {markets.length > 0 && (
              <label
                style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", marginTop: 10 }}
              >
                <input
                  type="checkbox"
                  checked={featureAllMarkets}
                  onChange={(e) => {
                    setActiveRisk(null);
                    setFeatureAllMarkets(e.target.checked);
                  }}
                />
                <span style={{ fontSize: 13.5 }}>
                  Make sure every selected market is featured on the slip, then fill up to the leg cap by
                  cycling through them again -- instead of just whichever market wins each match's own
                  probability contest
                </span>
              </label>
            )}
          </div>
        )}

        {isMobile && step === 3 && (
          <div className="wizard-review">
            <div className="meta" style={{ marginBottom: 6 }}>
              Ready to generate with:
            </div>
            <ul className="wizard-review-list">
              <li>
                {selectedDate
                  ? new Date(`${selectedDate}T00:00:00`).toLocaleDateString(undefined, {
                      weekday: "short", month: "short", day: "numeric",
                    })
                  : `Next ${daysAhead} days`}
              </li>
              <li>{selectedLeagues.length === 0 ? "Big 8 (default)" : selectedLeagues.join(", ")}</li>
              <li>
                Up to {maxLegs} legs, {(minProbability * 100).toFixed(0)}%+ accuracy each
              </li>
              <li>
                {markets.length === 0 ? DEFAULT_MARKET_PRESET.label : markets.join(", ")}
                {featureAllMarkets && markets.length > 0 ? " -- featured, then filled to the leg cap" : ""}
              </li>
            </ul>
          </div>
        )}

        {/* Back/Next only step through the form above -- Generate always runs
            the search against whatever criteria are currently set,
            regardless of which step that leaves visible on mobile. */}
        <div style={{ marginTop: 16, display: "flex", gap: 10 }}>
          {isMobile && step > 0 && (
            <button className="btn ghost" onClick={() => setWizardStep((s) => s - 1)}>
              ← Back
            </button>
          )}
          {isMobile && step < WIZARD_STEPS.length - 1 ? (
            <button className="btn" onClick={() => setWizardStep((s) => s + 1)}>
              Next →
            </button>
          ) : (
            <button className="btn" onClick={() => handlePreview()} disabled={previewing}>
              {previewing ? "Finding selections…" : "Generate selections"}
            </button>
          )}
        </div>
        {(!isMobile || step === WIZARD_STEPS.length - 1) && (
          <p className="setting-note" style={{ marginTop: 8 }}>
            Takes 5 to 10 seconds to load.
          </p>
        )}
      </div>

      {previewError && <ErrorState message={previewError} />}

      {preview && (
        <div className="card card-pad" style={{ marginBottom: 20 }} ref={resultsCardRef}>
          {fromExternalPicks && (
            <p className="setting-note" style={{ marginBottom: 12 }}>
              Resolved from your selected picks -- checked against each match's current prediction, just
              not run through the criteria form below.
            </p>
          )}
          {editingPickId !== null && (
            <p className="setting-note" style={{ marginBottom: 12 }}>
              Editing &ldquo;{adminPicks.find((p) => p.id === editingPickId)?.label || "this slip"}&rdquo; -- remove
              legs below, then Save changes.{" "}
              <button className="btn ghost" style={{ padding: "2px 8px", fontSize: 12 }} onClick={handleCancelEdit}>
                Cancel
              </button>
            </p>
          )}
          <div className="section-header" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>
              {editedLegs.length} leg{editedLegs.length === 1 ? "" : "s"}
              {editedLegs.length !== preview.legs.length ? ` -- edited from ${preview.legs.length}` : ""}
            </h3>
            <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {editedLegs.length > 0 && (
                <span className="meta tabular-nums" style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  {(editedCombinedProbability * 100).toFixed(0)}% combined probability -- lands {chanceLabel(editedCombinedProbability)}
                  <span className={`risk-tag ${resultRiskLabel(editedCombinedProbability).tone}`}>
                    {resultRiskLabel(editedCombinedProbability).label}
                  </span>
                </span>
              )}
              {!fromExternalPicks && editingPickId === null && (
                <button
                  type="button"
                  className="btn ghost"
                  style={{ padding: "4px 10px", fontSize: 12.5 }}
                  onClick={handleRefresh}
                  disabled={previewing}
                  title={
                    lastRunWasRandom
                      ? "Draw a new random slip with the same criteria"
                      : "Run the same criteria again"
                  }
                >
                  {previewing ? "Generating…" : "↻ Refresh"}
                </button>
              )}
            </span>
          </div>

          {editedLegs.length === 0 ? (
            <EmptyState
              icon="◌"
              title={
                preview.legs.length === 0
                  ? fromExternalPicks
                    ? "None of those picks matched a real, upcoming outcome -- see the warnings below."
                    : "No matches qualify for these criteria."
                  : "Every leg was removed -- generate again, or cancel editing, to start over."
              }
            />
          ) : (
            <>
              <p className="scroll-hint cards-on-mobile-hint">Swipe to see every column →</p>
              <div className="predictions-table-wrapper">
              <table className="predictions-table wide linear-on-mobile">
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Market</th>
                    <th>Selection</th>
                    <th>Probability</th>
                    <th>Kickoff</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {editedLegs.map((leg) => (
                    <tr key={`${leg.match_id}-${leg.market}-${leg.selection}`}>
                      <td data-label="Match">
                        <div className="match-cell">
                          {leg.home_team} vs {leg.away_team}
                        </div>
                        <div className="sub">{leg.league}</div>
                        {/* Folds Market/Selection/Probability/Kickoff into one
                            flowing line for the phone layout below -- a linear
                            row, not four separately labeled blocks the way
                            .cards-on-mobile stacks them. Hidden on desktop,
                            which already shows these as their own columns. */}
                        <div className="sub leg-mobile-meta">
                          {marketLabel(leg.market)}: {leg.selection} · {(leg.model_probability * 100).toFixed(0)}% ·{" "}
                          {new Date(leg.kickoff).toLocaleString(undefined, {
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </div>
                      </td>
                      <td className="sub" data-label="Market">{marketLabel(leg.market)}</td>
                      <td data-label="Selection">
                        <strong>{leg.selection}</strong>
                      </td>
                      <td className="tabular-nums" data-label="Probability">{(leg.model_probability * 100).toFixed(0)}%</td>
                      <td className="sub" data-label="Kickoff">
                        {new Date(leg.kickoff).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </td>
                      <td data-label="">
                        <button
                          className="btn ghost"
                          title="Remove this leg"
                          style={{ padding: "2px 8px", fontSize: 12 }}
                          onClick={() => removeLeg(leg)}
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </>
          )}

          {preview.warnings.map((w, i) => (
            <p className="setting-note" style={{ marginTop: 10 }} key={i}>
              {w}
            </p>
          ))}

          {editedLegs.length > 0 && (
            <div style={{ marginTop: 14, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <CopyButton text={formatLegsForCopy(editedLegs, editedCombinedProbability)} label="Copy selections" />
              {user?.role === "superadmin" && (
                <button className="btn ghost" onClick={handleFeatureOrSave} disabled={featuring}>
                  {featuring
                    ? editingPickId !== null
                      ? "Saving…"
                      : "Featuring…"
                    : editingPickId !== null
                      ? "💾 Save changes"
                      : "★ Feature this slip"}
                </button>
              )}
            </div>
          )}

          {editedLegs.length > 0 && (
            <SiteCodes
              legs={editedLegs}
              book={(sites) =>
                bookPicks(
                  editedLegs.map((l) => ({ match_id: l.match_id, market: l.market, selection: l.selection })),
                  sites,
                )
              }
            />
          )}
        </div>
      )}

      {editedLegs.length > 0 && (
        <StickySlipBar
          summary={`${editedLegs.length} leg${editedLegs.length === 1 ? "" : "s"} · ${(editedCombinedProbability * 100).toFixed(0)}% combined`}
          copyText={formatLegsForCopy(editedLegs, editedCombinedProbability)}
          onView={() => resultsCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
        />
      )}

      {user?.role === "superadmin" && adminPicks.length > 0 && (
        <div className="card card-pad" style={{ marginTop: 20 }}>
          <div className="section-header" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Currently featured Admin Picks</h3>
            <span className="meta">Live on every Dashboard right now</span>
          </div>
          {adminPicks.map((pick) => (
            <div
              key={pick.id}
              className="match-meta-row"
              style={{ justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}
            >
              <span>
                {pick.label ? <strong>{pick.label}</strong> : <span className="sub">Untitled slip</span>}{" "}
                {pick.source?.startsWith("system_weekly_") && (
                  <span className="sub" style={{ fontStyle: "italic" }}>
                    (auto-generated, weekly){" "}
                  </span>
                )}
                <span className="sub">
                  {pick.legs.length} leg{pick.legs.length === 1 ? "" : "s"} ·{" "}
                  {pick.priced && pick.combined_odds !== null ? `${pick.combined_odds.toFixed(2)} odds · ` : ""}
                  {(pick.combined_probability * 100).toFixed(0)}% probability ({chanceLabel(pick.combined_probability)})
                </span>
                {pick.result !== "pending" && (
                  <span className={`result-tag ${pick.result}`} style={{ marginLeft: 8 }}>
                    {pick.result === "won" ? "Won" : pick.result === "lost" ? "Lost" : "Confirming result"}
                  </span>
                )}
                {pick.featured_public && (
                  <span className="badge-neutral" style={{ marginLeft: 8 }}>
                    In public track record
                  </span>
                )}
                {/* pick.risk_tier is combined-probability-based (see
                    betcode.selection.risk_tier) -- correct for a hand-built
                    slip, but not for a fixed 10-leg weekly accumulator built
                    to a combined-*odds* target instead (see AdminPickCard's
                    riskOverride doc). For one of those, the label's own
                    "Low/Medium/High Risk" text is the actual tier -- this
                    tag is only ever shown here for a hand-built slip. */}
                {!pick.source?.startsWith("system_weekly_") && (
                  <span className={`risk-tag ${pick.risk_tier}`} style={{ marginLeft: 8 }}>
                    {pick.risk_tier === "low" ? "Low risk" : pick.risk_tier === "medium" ? "Medium risk" : "High risk"}
                  </span>
                )}
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                {pick.result === "pending" && (
                  <button
                    className="btn ghost"
                    style={{ padding: "2px 8px", fontSize: 12 }}
                    disabled={pickBusy === pick.id}
                    onClick={() => handlePromoteToTeaser(pick.id)}
                    title="Shows this slip's own legs as the public teaser on the Welcome page, replacing whatever's showing now."
                  >
                    {pickBusy === pick.id ? "Promoting..." : "Use as public teaser"}
                  </button>
                )}
                {pick.result === "unresolved" && (
                  <>
                    <button
                      className="btn ghost"
                      style={{ padding: "2px 8px", fontSize: 12 }}
                      disabled={pickBusy === pick.id}
                      onClick={() => handleSetPickResult(pick.id, "won")}
                      title="This pick's market can't be auto-graded -- confirm the result by hand."
                    >
                      Mark won
                    </button>
                    <button
                      className="btn ghost"
                      style={{ padding: "2px 8px", fontSize: 12 }}
                      disabled={pickBusy === pick.id}
                      onClick={() => handleSetPickResult(pick.id, "lost")}
                    >
                      Mark lost
                    </button>
                  </>
                )}
                {pick.track_record_open ? (
                  <button
                    className="btn ghost"
                    style={{ padding: "2px 8px", fontSize: 12 }}
                    disabled={pickBusy === pick.id}
                    onClick={() => handleToggleFeaturedPublic(pick.id, !pick.featured_public)}
                    title="Commit this slip to the public track record on the Welcome page. From kickoff on it stays there, win or lose."
                  >
                    {pick.featured_public ? "Withdraw from track record" : "Commit to track record"}
                  </button>
                ) : (
                  pick.featured_public && <span className="sub">In the public track record</span>
                )}
                {!pick.priced && (
                  <button
                    className="btn ghost"
                    style={{ padding: "2px 8px", fontSize: 12 }}
                    disabled={pickBusy === pick.id}
                    onClick={() => handleEditPick(pick)}
                  >
                    Edit
                  </button>
                )}
                <button
                  className="btn ghost"
                  style={{ padding: "2px 8px", fontSize: 12 }}
                  disabled={pickBusy === pick.id}
                  onClick={() => handleRemoveAdminPick(pick.id)}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {user?.role === "superadmin" && (
        <div className="card card-pad" style={{ marginTop: 20 }} ref={teaserCardRef}>
          <div className="section-header" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Public teaser pick</h3>
            <span className="meta">Shows real selections on the Welcome page -- no login required</span>
          </div>

          {teaserJustPromoted && (
            <p className="meta" style={{ color: "var(--accent)", marginBottom: 10 }}>
              Promoted -- this is now live on the Welcome page.
            </p>
          )}

          {teaserPick?.has_data ? (
            <>
              {teaserPick.legs.map((leg, i) => (
                <div
                  key={`${leg.match_id}-${leg.market}-${leg.selection}`}
                  className="match-meta-row"
                  style={{
                    justifyContent: "space-between",
                    padding: "6px 0",
                    borderTop: i === 0 ? undefined : "1px solid var(--border)",
                  }}
                >
                  <span className="sub">
                    {leg.home_team} vs {leg.away_team} -- {marketLabel(leg.market)}: {leg.selection}
                  </span>
                  <span className="badge-neutral">{leg.decimal_odds?.toFixed(2)}</span>
                </div>
              ))}
              <p className="meta" style={{ marginTop: 8 }}>
                Combined odds: {teaserPick.combined_odds.toFixed(2)}x -- lands {chanceLabel(teaserPick.combined_probability)}
              </p>
              <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
                <button className="btn ghost" disabled={teaserBusy} onClick={handleClearTeaser}>
                  Remove
                </button>
              </div>
            </>
          ) : (
            <p className="meta">
              Nothing showing right now -- pick a pending slip above and use its "Use as public teaser" button.
            </p>
          )}
        </div>
      )}

      <MyCodesHistory />
    </div>
  );
}
