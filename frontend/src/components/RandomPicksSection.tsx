import { AdminPickCard } from "./AdminPickCard";
import { PickBookingCodes } from "./PickBookingCodes";
import { useAuth } from "../lib/AuthContext";
import type { AdminPick } from "../types";

type Cadence = "daily" | "weekly";

function cadenceOf(pick: AdminPick): Cadence | null {
  if (typeof pick.source !== "string") return null;
  if (pick.source.startsWith("system_random_daily_")) return "daily";
  if (pick.source.startsWith("system_random_weekly_")) return "weekly";
  return null;
}

const GROUPS: { cadence: Cadence; title: string; blurb: string }[] = [
  {
    cadence: "daily",
    title: "Random Daily Picks",
    blurb: "Two randomly drawn slips, reshuffled every day -- 60%+ picks only, not ranked by risk level.",
  },
  {
    cadence: "weekly",
    title: "Random Weekly Picks",
    blurb: "Two randomly drawn slips, reshuffled every week -- 60%+ picks only, not ranked by risk level.",
  },
];

/** scripts/generate_random_picks.py's output -- COUNT (2) random-sized
 * (10-15 leg) slips per cadence, drawn from matches at 60%+ model
 * probability rather than ranked or filtered by risk tier (see
 * AdminPickCard's hideRisk). Same "no empty-state" reasoning as
 * AdminPicksSection/WeeklyPicksSection: a cadence that hasn't generated yet
 * just doesn't render.
 *
 * Each card carries its own live booking panel (PickBookingCodes), unlike a
 * hand-curated Admin Pick's admin-typed code -- these were never built on a
 * real bookmaker by a human, so the only code available is one a betting
 * site issues on request.
 *
 * ``allPicks`` is the full GET /api/predictions/admin-picks response,
 * fetched once by the caller (AIPicks) and shared across this section,
 * AdminPicksSection and WeeklyPicksSection -- see AIPicks's own docstring
 * for why this section no longer fetches it itself. */
export function RandomPicksSection({ picks: allPicks }: { picks: AdminPick[] | null }) {
  const { user, accessStatus } = useAuth();
  const viewerHasPremium = user?.role === "superadmin" || !!accessStatus?.has_access;

  if (!allPicks) return null;
  const picks = allPicks.filter((pick) => cadenceOf(pick) !== null);

  return (
    <>
      {GROUPS.map(({ cadence, title, blurb }) => {
        const group = picks.filter((p) => cadenceOf(p) === cadence);
        if (group.length === 0) return null;
        return (
          <div className="section-reveal" key={cadence}>
            <div className="section-header">
              <h2>{title}</h2>
              <span className="meta">{blurb}</span>
            </div>
            <div className="grid">
              {group.map((pick) => (
                <div key={pick.id}>
                  <AdminPickCard pick={pick} hideRisk />
                  <PickBookingCodes pick={pick} viewerHasPremium={viewerHasPremium} />
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}
