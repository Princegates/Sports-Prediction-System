import type { BetCodePick } from "../types";

/**
 * A slip a signed-out visitor asked to turn into a booking code (the public
 * free picks). Booking needs an account, so it's kept here across sign-up,
 * email confirmation and sign-in, and handed to the AI Generation page the
 * first time they reach it. Per-browser and best-effort: storage can be
 * unavailable, in which case the visitor just builds the slip themselves.
 */

const KEY = "si.pendingSlip";
// Long enough to confirm an email and come back; short enough that a stale
// slip of finished matches isn't offered days later.
const MAX_AGE_MS = 24 * 3_600_000;

export function savePendingSlip(picks: BetCodePick[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now(), picks }));
  } catch {
    /* storage unavailable -- nothing to keep */
  }
}

function read(): BetCodePick[] | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const { savedAt, picks } = JSON.parse(raw) as { savedAt: number; picks: BetCodePick[] };
    if (Date.now() - savedAt > MAX_AGE_MS || !Array.isArray(picks) || picks.length === 0) {
      localStorage.removeItem(KEY);
      return null;
    }
    return picks;
  } catch {
    return null;
  }
}

export function hasPendingSlip(): boolean {
  return read() !== null;
}

/** Returns the saved slip, if any, and forgets it. */
export function takePendingSlip(): BetCodePick[] | null {
  const picks = read();
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  return picks;
}
