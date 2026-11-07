/**
 * The first-run walkthrough: what it says, and who gets it.
 *
 * Kept as plain data so the wording can be changed here without touching
 * the dialog. Every claim in `body` is something the app really does -- if a
 * feature changes, change its step.
 */

export interface TourStep {
  id: string;
  title: string;
  body: string;
  /** Short extra points shown under the body. */
  points?: string[];
  /** Where "Take me there" goes; omitted for steps that are only explanation. */
  to?: string;
  toLabel?: string;
  /** Which icon to show (see TourDialog's ICONS). */
  icon: "mascot" | "home" | "chart" | "markets" | "picks" | "generate" | "live" | "guda" | "access";
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: "welcome",
    icon: "mascot",
    title: "Welcome to Socca Intelligence",
    body:
      "We turn match data into probabilities: for each game, how likely every outcome is, with the reasoning shown next to the number. This quick tour (under a minute) shows where everything is.",
    points: ["Predictions are probabilities, not guarantees.", "You can reopen this tour any time from the account menu."],
  },
  {
    id: "dashboard",
    icon: "home",
    title: "Dashboard: your starting point",
    body: "Today's most confident picks and the slips we've featured, across the leagues you choose.",
    points: [
      "Use the league menu at the top to narrow to one competition.",
      "The search icon (or Ctrl/⌘ + K) jumps straight to any team.",
    ],
    to: "/app",
    toLabel: "Open the Dashboard",
  },
  {
    id: "predictions",
    icon: "chart",
    title: "Predictions: every upcoming match",
    body:
      "Win, draw and loss probabilities for each fixture, plus a confidence rating so you can see where the model is surest.",
    points: ["Sort by kickoff, probability or confidence.", "Tap any match for the full breakdown and why the model thinks what it does."],
    to: "/app/predictions",
    toLabel: "See predictions",
  },
  {
    id: "markets",
    icon: "markets",
    title: "Markets: beyond win/draw/lose",
    body:
      "A bookmaker-style coupon, priced by our model instead of a bookmaker: goals totals, both teams to score, double chance, correct score and more.",
    to: "/app/markets",
    toLabel: "Browse markets",
  },
  {
    id: "picks",
    icon: "picks",
    title: "AI Picks: ready-made slips",
    body:
      "Multi-match slips put together for you every day and every week, in Low, Medium and High risk. Low risk slips use only very likely legs; High risk reaches further and is much harder to land.",
    points: ["More legs means a smaller chance that all of them come in.", "Each slip shows its combined probability, so you know what you're looking at."],
    to: "/app/ai-picks",
    toLabel: "See AI Picks",
  },
  {
    id: "generate",
    icon: "generate",
    title: "AI Generation: build your own",
    body:
      "Choose the leagues, how many matches and how safe you want it, and we'll assemble the selections for you. Where a betting site is supported you can also get a booking code to load the slip.",
    points: ["Each run uses one of your daily runs. The count is shown on the page.", "Need more? Extra runs can be added when you run out."],
    to: "/app/betcodes",
    toLabel: "Try AI Generation",
  },
  {
    id: "live",
    icon: "live",
    title: "Live scores and Guda",
    body:
      "Follow matches as they happen on the Live tab. And whenever you have a question, tap Guda, the chat assistant in the corner: ask about a match, compare two teams or find a safer pick.",
    points: ["Guda's full conversations are part of full access."],
    to: "/app/live",
    toLabel: "Open Live",
  },
  {
    id: "access",
    icon: "access",
    title: "Your access, and playing it safe",
    body:
      "New accounts get a free trial. Confirm your email to start it. To keep full access afterwards, redeem an access code on the Access code page, or ask the admin on WhatsApp.",
    points: [
      "Won with our picks? Share it from “Share a win”.",
      "These are analyses, not promises. Only ever stake what you can afford to lose, and take a break if it stops being fun. 18+ only.",
    ],
    to: "/app/access",
    toLabel: "Go to Access code",
  },
];

/** Bump to show the tour again to everyone after a big redesign. */
const TOUR_VERSION = 1;

/** Only accounts this young get the tour on their own: it's for people
 * finding their way in, not for the existing members. Anyone can still open
 * it from the account menu. */
const NEW_ACCOUNT_DAYS = 14;

const seenKey = (userId: number) => `tour_seen_v${TOUR_VERSION}_${userId}`;

export function hasSeenTour(userId: number): boolean {
  try {
    return localStorage.getItem(seenKey(userId)) === "1";
  } catch {
    return true; // storage blocked: don't risk showing it on every page load
  }
}

export function markTourSeen(userId: number): void {
  try {
    localStorage.setItem(seenKey(userId), "1");
  } catch {
    // ignore
  }
}

export function shouldAutoShowTour(user: { id: number; role: string; created_at: string }, now = Date.now()): boolean {
  if (user.role === "superadmin") return false;
  if (hasSeenTour(user.id)) return false;
  const created = Date.parse(user.created_at.endsWith("Z") ? user.created_at : `${user.created_at}Z`);
  if (Number.isNaN(created)) return false;
  return now - created < NEW_ACCOUNT_DAYS * 24 * 60 * 60 * 1000;
}

/** Event the account menu fires to reopen the tour from anywhere. */
export const OPEN_TOUR_EVENT = "open-tour";
