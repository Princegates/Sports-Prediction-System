import { useEffect, useState } from "react";
import { fetchBranding } from "../api";
import { AdminPickCard } from "./AdminPickCard";
import type { AdminPick } from "../types";

/** Renders nothing rather than an empty-state, same reasoning as
 * GudaPicksSection -- no featured slips right now (or the operator having
 * turned the whole thing off) is a normal, unremarkable state.
 *
 * ``allPicks`` is the full GET /api/predictions/admin-picks response,
 * fetched once by the caller (AIPicks) and shared across this section,
 * WeeklyPicksSection and RandomPicksSection -- see AIPicks's own docstring
 * for why this section no longer fetches it itself. ``null`` means "hasn't
 * loaded yet", not "empty". */
export function AdminPicksSection({ picks: allPicks }: { picks: AdminPick[] | null }) {
  const [whatsapp, setWhatsapp] = useState<string | null>(null);

  // The three weekly system accumulators live in this same list -- see
  // WeeklyPicksSection, which filters the same shared data for its own,
  // distinctly-labeled section, so they aren't duplicated here.
  const picks = allPicks?.filter((pick) => !pick.source) ?? null;

  // Only fetched once some pick actually needs the "subscribe to see this
  // code" CTA (has_booking_code true, booking_code still null -- a
  // free-tier viewer) -- most picks never carry a booking code at all.
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
        <h2>Admin Picks</h2>
        <span className="meta">Multi-leg slips our team has put together, risk factor included</span>
      </div>
      <div className="picks-grid">
        {picks.map((pick) => (
          <AdminPickCard key={pick.id} pick={pick} whatsapp={whatsapp} />
        ))}
      </div>
    </div>
  );
}
