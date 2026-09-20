import { useEffect, useState } from "react";
import { fetchAdminPicks, fetchBranding } from "../api";
import { AdminPickCard } from "./AdminPickCard";
import type { AdminPick } from "../types";

/** Renders nothing rather than an empty-state, same reasoning as
 * GudaPicksSection -- no featured slips right now (or the operator having
 * turned the whole thing off) is a normal, unremarkable state. */
export function AdminPicksSection() {
  const [picks, setPicks] = useState<AdminPick[] | null>(null);
  const [whatsapp, setWhatsapp] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminPicks()
      .then((p) => !cancelled && setPicks(p))
      .catch(() => !cancelled && setPicks([]));
    return () => {
      cancelled = true;
    };
  }, []);

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
    <>
      <div className="section-header">
        <h2>Admin Picks</h2>
        <span className="meta">Multi-leg slips our team has put together, risk factor included</span>
      </div>
      <div className="grid">
        {picks.map((pick) => (
          <AdminPickCard key={pick.id} pick={pick} whatsapp={whatsapp} />
        ))}
      </div>
    </>
  );
}
