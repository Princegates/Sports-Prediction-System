import { useEffect, useState } from "react";
import { fetchAdminPicks } from "../api";
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
    blurb: "5 randomly drawn slips, reshuffled every day -- 60%+ picks only, not ranked by risk level.",
  },
  {
    cadence: "weekly",
    title: "Random Weekly Picks",
    blurb: "5 randomly drawn slips, reshuffled every week -- 60%+ picks only, not ranked by risk level.",
  },
];

/** scripts/generate_random_picks.py's output -- COUNT (5) random-sized
 * (10-15 leg) slips per cadence, each drawn from matches at 60%+ model
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
 * ``onLoaded`` fires after every fetch (success or failure) -- see
 * GudaPicksSection's own docstring for why. */
export function RandomPicksSection({ onLoaded }: { onLoaded?: () => void } = {}) {
  const { user, accessStatus } = useAuth();
  const viewerHasPremium = user?.role === "superadmin" || !!accessStatus?.has_access;
  const [picks, setPicks] = useState<AdminPick[] | null>(null);

  useEffect(() => {
    let cancelled = false;

    function load() {
      fetchAdminPicks()
        .then((p) => !cancelled && setPicks(p.filter((pick) => cadenceOf(pick) !== null)))
        .catch(() => !cancelled && setPicks([]))
        .finally(() => !cancelled && onLoaded?.());
    }

    load();
    const interval = setInterval(load, 60_000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (!picks) return null;

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
