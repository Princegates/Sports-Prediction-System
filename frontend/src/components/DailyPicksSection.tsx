import { useEffect, useState } from "react";
import { fetchBranding } from "../api";
import { AdminPickCard } from "./AdminPickCard";
import { PickBookingCodes } from "./PickBookingCodes";
import { useAuth } from "../lib/AuthContext";
import type { AdminPick } from "../types";

const TIER_ORDER = ["low", "medium", "high"] as const;

function tierOf(pick: AdminPick): (typeof TIER_ORDER)[number] | null {
  for (const tier of TIER_ORDER) {
    if (pick.source === `system_daily_${tier}`) return tier;
  }
  return null;
}

/** The three system-generated daily accumulators (Low / Medium / High risk,
 * 10-15 legs each, pooled across the biggest leagues) that app.risk_picks
 * publishes -- see AdminPick.source. What sets a tier is how likely each
 * *leg* is (Low 85%+, Medium 70-85%, High 55-70%), not a combined price, so
 * they exist even on days with no bookmaker odds captured.
 *
 * Each is replaced by a fresh slip once its first match is about to kick
 * off, so what's shown here is always still fully bookable. Renders nothing
 * until a tier has been generated, same "no empty-state" reasoning as the
 * other pick sections.
 *
 * ``riskOverride`` is passed to the card because ``pick.risk_tier`` is
 * derived from the *combined* probability, which for a 10-15 leg slip is
 * "high" for every tier -- it would contradict the section's own Low /
 * Medium / High labelling right beside it (same reason WeeklyPicksSection
 * sets it). ``allPicks`` is the shared GET /api/predictions/admin-picks
 * response fetched once by AIPicks. */
export function DailyPicksSection({ picks: allPicks }: { picks: AdminPick[] | null }) {
  const { user, accessStatus } = useAuth();
  const viewerHasPremium = user?.role === "superadmin" || !!accessStatus?.has_access;
  const [whatsapp, setWhatsapp] = useState<string | null>(null);

  const picks = allPicks
    ? allPicks
        .filter((pick) => tierOf(pick) !== null)
        .sort((a, b) => TIER_ORDER.indexOf(tierOf(a)!) - TIER_ORDER.indexOf(tierOf(b)!))
    : null;

  const needsWhatsapp = picks?.some((p) => p.has_booking_code && !p.booking_code) ?? false;
  useEffect(() => {
    if (!needsWhatsapp || whatsapp) return;
    fetchBranding()
      .then((b) => setWhatsapp(b.contact_whatsapp || null))
      .catch(() => {});
  }, [needsWhatsapp, whatsapp]);

  if (!picks || picks.length === 0) return null;

  return (
    <div className="section-reveal">
      <div className="section-header">
        <h2>Today's Picks</h2>
        <span className="meta">
          10-15 leg accumulators, refreshed daily -- Low (every leg 85%+ likely), Medium (70-85%) and High (55-70%)
        </span>
      </div>
      <div className="picks-grid">
        {picks.map((pick) => (
          <div key={pick.id}>
            <AdminPickCard pick={pick} whatsapp={whatsapp} riskOverride={tierOf(pick) ?? undefined} />
            <PickBookingCodes pick={pick} viewerHasPremium={viewerHasPremium} />
          </div>
        ))}
      </div>
    </div>
  );
}
