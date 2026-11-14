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
  /** One or two short sentences, in plain English. */
  body: string;
  /** "What you can do here": short, one idea each. */
  points?: string[];
  /** Where to tap, said simply. Shown with the glowing spot on screen. */
  tryIt?: string;
  /** Said instead of `tryIt` when the thing isn't on screen (a phone has no top tabs, say). */
  tryItElse?: string;
  /** `data-tour` names of the real button to light up, best first; the first
   * one actually visible wins (a phone shows the bottom bar, a computer the top tabs). */
  target?: string[];
  /** Page to be on for the target to exist; the tour opens it for you. */
  route?: string;
  icon: "mascot" | "home" | "league" | "search" | "chart" | "markets" | "picks" | "generate" | "live" | "share" | "access" | "theme" | "menu";
}

/** Which `data-tour` name each main page's button wears, in both the top tabs
 * (computer) and the bottom bar (phone). */
const TARGET_BY_PATH: Record<string, string> = {
  "/app": "nav-dashboard",
  "/app/ai-picks": "nav-ai-picks",
  "/app/predictions": "nav-predictions",
  "/app/markets": "nav-markets",
  "/app/betcodes": "nav-generate",
  "/app/live": "nav-live",
  "/app/share-win": "nav-share",
  "/app/access": "nav-access",
};

export function tourTargetFor(path: string): string | undefined {
  return TARGET_BY_PATH[path];
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: "welcome",
    icon: "mascot",
    title: "Welcome! Let's take a quick look around",
    body:
      "This app helps you pick football games. For every game, we work out how likely each result is, and we show you why. This guide takes about two minutes.",
    points: [
      "Nobody can promise a win. We show chances, not guarantees.",
      "A glowing spot will show you exactly where to tap.",
      "You can stop any time and come back later from the account menu.",
    ],
  },
  {
    id: "dashboard",
    icon: "home",
    title: "Dashboard: your home page",
    body: "This is the first page you see every time you open the app.",
    points: [
      "“Today's headline picks” shows our best pick in each league today.",
      "With full access you also get “Match discovery”. Use Today and Tomorrow to find games.",
      "The search bar at the top finds any team.",
    ],
    tryIt: "Tap the glowing button to come back to this page at any time.",
    target: ["nav-dashboard"],
    route: "/app",
  },
  {
    id: "league",
    icon: "league",
    title: "League menu: choose a league",
    body: "A league is a competition, like the Premier League or La Liga. This menu shows games from one league only.",
    points: ["Choose “All leagues” to see every game again."],
    tryIt: "Tap the glowing menu and pick a league.",
    tryItElse: "Look for the league menu at the top of the Dashboard.",
    target: ["league-select"],
    route: "/app",
  },
  {
    id: "search",
    icon: "search",
    title: "Search: find any team",
    body: "Type a team's name to open its full profile and see how we rate it.",
    points: ["On a computer you can also press Ctrl + K (or ⌘ + K on a Mac)."],
    tryIt: "Tap the glowing magnifying glass.",
    target: ["search"],
  },
  {
    id: "predictions",
    icon: "chart",
    title: "Predictions: our pick for every game",
    body: "A list of coming games. Each one shows our top pick and how likely it is, as a percentage.",
    points: [
      "“Confidence” tells you how sure we are: High, Medium or Low.",
      "Use the buttons at the top to show only High, Medium or Low games.",
      "“Table” shows a simple list. “Confidence tiers” groups the games from safest to riskiest.",
      "Tap any game to read why we chose that pick.",
      "Tick some games and press “Send to AI Generation” to turn them into a slip.",
    ],
    tryIt: "Tap the glowing button, then try the confidence buttons.",
    target: ["nav-predictions"],
  },
  {
    id: "markets",
    icon: "markets",
    title: "Markets: see every possible result",
    body:
      "You can bet on many things in one game: who wins, how many goals, and more. Each kind of bet is called a market. Here you can see all of them, with our chances.",
    points: [
      "Tap a result to add it to “My picks”. A bar shows your picks and how likely it is that all of them win.",
      "Switch between “Coupon” and “List by probability” with the two buttons at the top.",
      "“Choose markets” shows only the kinds of bets you care about.",
      "“Browse by date” lets you pick a day.",
      "Press Copy to copy your picks.",
    ],
    tryIt: "Tap the glowing button.",
    target: ["nav-markets"],
  },
  {
    id: "picks",
    icon: "picks",
    title: "AI Picks: slips made for you",
    body:
      "A slip is a group of games you choose together. For a slip to win, every game in it must win. We make new slips for you every day and every week.",
    points: [
      "85%+ games: only games we think are very likely to win.",
      "70 to 85% games: likely games.",
      "55 to 70% games: less sure games. Much harder to win.",
      "These slips have 10 to 15 games, and more games means a smaller chance that all of them win. Each slip shows its own chance as a percentage, and its risk tag goes by that.",
    ],
    tryIt: "Tap the glowing button to see today's and this week's slips.",
    target: ["nav-ai-picks"],
  },
  {
    id: "generate",
    icon: "generate",
    title: "AI Generation: build your own slip",
    body: "Tell us what you want, and we pick the games for you.",
    points: [
      "Tap Low, Medium or High risk to fill in the settings for you. Or set them yourself.",
      "Choose the leagues, the days to look at, how many games you want, and the lowest chance you will accept (like 65% or more).",
      "Look over the games we found. Remove any you don't like.",
      "Copy your games, or get a booking code. A booking code is a short code you type into a betting site to load all your games at once. It works on the sites we support.",
      "Each time you generate, you use 1 run. The number of runs you have left today is shown on the page.",
    ],
    tryIt: "Tap the glowing button, then try “Low risk”.",
    target: ["nav-generate"],
  },
  {
    id: "live",
    icon: "live",
    title: "Live: scores as they happen",
    body: "Follow games while they are being played.",
    points: [
      "Choose Yesterday, Today or Tomorrow.",
      "Choose “All” to see every game, or “Live” to see only the games being played right now.",
    ],
    tryIt: "Tap the glowing button.",
    target: ["nav-live"],
  },
  {
    id: "share",
    icon: "share",
    title: "Share a win: show off your win",
    body: "Won with our picks? Post your winning ticket so other people can see it.",
    points: [
      "A screenshot or a PDF of your ticket both work. For PDFs we remove your account link and IP address.",
      "Our team checks each one before it shows on the site. This usually takes less than a day.",
    ],
    tryIt: "Tap the glowing button when you have a win to share.",
    target: ["nav-share"],
  },
  {
    id: "access",
    icon: "access",
    title: "Access code: keep full access",
    body:
      "New accounts get a free trial. It starts when you confirm your email, so check your inbox (and your spam folder).",
    points: [
      "When the trial ends, type an access code here to keep full access.",
      "Don't have a code? Message the admin on WhatsApp.",
    ],
    tryIt: "On a computer, tap the glowing “Access code” button. On a phone, open the account menu and choose “Access code”.",
    target: ["nav-access", "account-menu"],
  },
  {
    id: "theme",
    icon: "theme",
    title: "Light mode and dark mode",
    body: "Choose the one that is easier on your eyes. We remember your choice.",
    tryIt: "Tap the glowing sun or moon button to switch.",
    target: ["theme-toggle"],
  },
  {
    id: "menu",
    icon: "menu",
    title: "Account menu: your profile and more",
    body: "Tap your picture at the top right to open this menu.",
    points: [
      "Profile: change your name or password, choose your emails, and take a break from the app.",
      "Access code: enter a code to unlock more.",
      "Share a win: post a winning ticket.",
      "Take the tour: see this guide again.",
      "Sign out: leave your account.",
    ],
    tryIt: "Tap the glowing picture.",
    target: ["account-menu"],
  },
  {
    id: "guda",
    icon: "mascot",
    title: "Guda: your helper",
    body: "Guda is our helper. Tap the “Ask Guda” button at the bottom right and type a question, like you would send a message to a friend.",
    points: [
      "Try: “Who is likely to win Arsenal vs Chelsea?” or “Compare Barcelona and Real Madrid.”",
      "If the button is in the way, drag it somewhere else.",
      "The full chat is part of full access.",
    ],
    tryIt: "Look for the glowing button.",
    target: ["guda"],
  },
  {
    id: "safe",
    icon: "mascot",
    title: "Before you start: play it safe",
    body: "These are chances, not promises. Nobody can know for sure who will win a game.",
    points: [
      "Only use money you can afford to lose.",
      "You must be 18 or older.",
      "If it stops being fun, take a break. You can pause your account from your Profile.",
      "To see this guide again, open the account menu and tap “Take the tour”.",
    ],
  },
];

/** Bump to bring the tour back for people who had switched it off. */
const TOUR_VERSION = 1;

const optOutKey = (userId: number) => `tour_optout_v${TOUR_VERSION}_${userId}`;
const sessionKey = (userId: number) => `tour_session_v${TOUR_VERSION}_${userId}`;

/** "Don't show this again" -- the only thing that stops it for good. */
export function hasOptedOutOfTour(userId: number): boolean {
  try {
    return localStorage.getItem(optOutKey(userId)) === "1";
  } catch {
    return false;
  }
}

export function setTourOptOut(userId: number, optedOut: boolean): void {
  try {
    if (optedOut) localStorage.setItem(optOutKey(userId), "1");
    else localStorage.removeItem(optOutKey(userId));
  } catch {
    // ignore
  }
}

/** Per visit (tab or installed-app launch), so a refresh or a click to another
 * page doesn't bring it straight back, but opening the app again does. */
function shownThisVisit(userId: number): boolean {
  try {
    return sessionStorage.getItem(sessionKey(userId)) === "1";
  } catch {
    return true; // storage blocked: don't risk showing it on every page load
  }
}

export function markTourShownThisVisit(userId: number): void {
  try {
    sessionStorage.setItem(sessionKey(userId), "1");
  } catch {
    // ignore
  }
}

/** Members see the tour once: closing it in any way opts them out on this
 * device. Admins run the place and don't need it. Anyone can reopen it from
 * "Take the tour" in the account menu. */
export function shouldAutoShowTour(user: { id: number; role: string }): boolean {
  return user.role !== "superadmin" && !hasOptedOutOfTour(user.id) && !shownThisVisit(user.id);
}

/** Event the account menu fires to reopen the tour from anywhere. */
export const OPEN_TOUR_EVENT = "open-tour";
