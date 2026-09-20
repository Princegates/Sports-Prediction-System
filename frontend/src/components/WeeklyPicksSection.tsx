import { useEffect, useState } from "react";
import { fetchAdminPicks, fetchBranding } from "../api";
import { AdminPickCard } from "./AdminPickCard";
import type { AdminPick } from "../types";

const TIER_ORDER = ["low", "medium", "high"] as const;

function tierOf(pick: AdminPick): (typeof TIER_ORDER)[number] | null {
  for (const tier of TIER_ORDER) {
    if (pick.source === `system_weekly_${tier}`) return tier;
  }
  return null;
}

/** The three system-generated 10-leg accumulators (Low/Medium/High risk,
 * pooled across every competition) scripts/generate_weekly_picks.py
 * publishes once each match week -- see AdminPick.source. Renders nothing
 * when a tier hasn't been generated yet or couldn't be filled this week
 * (see that script's docstring for why it skips rather than publishes a
 * slip outside its own stated odds range), same "no empty-state" reasoning
 * AdminPicksSection already applies. */
export function WeeklyPicksSection() {
  const [picks, setPicks] = useState<AdminPick[] | null>(null);
  const [whatsapp, setWhatsapp] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminPicks()
      .then((p) => {
        if (cancelled) return;
        const weekly = p.filter((pick) => tierOf(pick) !== null);
        weekly.sort((a, b) => TIER_ORDER.indexOf(tierOf(a)!) - TIER_ORDER.indexOf(tierOf(b)!));
        setPicks(weekly);
      })
      .catch(() => !cancelled && setPicks([]));
    return () => {
      cancelled = true;
    };
  }, []);

  const needsWhatsapp = picks?.some((p) => p.has_booking_code && !p.booking_code) ?? false;
  useEffect(() => {
    if (!needsWhatsapp || whatsapp) return;
    fetchBranding()
      .then((b) => setWhatsapp(b.contact_whatsapp || null))
      .catch(() => {});
  }, [needsWhatsapp, whatsapp]);

  if (!picks || picks.length === 0) return null;

  return (
    <>
      <div className="section-header">
        <h2>This Week's Picks</h2>
        <span className="meta">
          10-leg accumulators across every competition -- Low (5-10 odds), Medium (11-20) and High (21-30)
        </span>
      </div>
      <div className="grid">
        {picks.map((pick) => (
          <AdminPickCard key={pick.id} pick={pick} whatsapp={whatsapp} riskOverride={tierOf(pick) ?? undefined} />
        ))}
      </div>
    </>
  );
}
