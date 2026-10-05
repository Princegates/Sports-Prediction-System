import { bookPicks } from "../api";
import { SiteCodes } from "./SiteCodes";
import type { AdminPick } from "../types";

/** Wraps SiteCodes with the premium gate booking-code generation needs on
 * top of pick visibility: a locked pick's own card already hides its legs,
 * so there's nothing to book here regardless of viewer. The one pick a
 * non-premium viewer *can* see still can't generate a code -- the backend's
 * /api/betcodes/picks router already requires active access
 * (require_active_access) for every call, so this is the UI-side half of
 * that same rule: a locked notice instead of a button that would just 403. */
export function PickBookingCodes({ pick, viewerHasPremium }: { pick: AdminPick; viewerHasPremium: boolean }) {
  if (pick.locked) return null;

  if (!viewerHasPremium) {
    return (
      <div
        className="match-meta-row"
        style={{ marginTop: 8, padding: "8px 10px", borderRadius: 8, background: "var(--bg-surface-2)" }}
      >
        🔒 Booking codes are premium members only.
      </div>
    );
  }

  return (
    <SiteCodes
      legs={pick.legs}
      book={(sites) =>
        bookPicks(
          pick.legs.map((l) => ({ match_id: l.match_id, market: l.market, selection: l.selection })),
          sites,
        )
      }
    />
  );
}
