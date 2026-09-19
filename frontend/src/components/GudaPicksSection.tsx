import { useEffect, useState } from "react";
import { fetchGudaPicks } from "../api";
import { GudaPickCard } from "./GudaPickCard";
import type { FeaturedPick } from "../types";

/** Renders nothing rather than an empty-state -- unlike the free/premium
 * sections above it, there being no picks right now (or the operator having
 * turned the whole thing off) is a normal, unremarkable state, not
 * something worth a "nothing here yet" card. */
export function GudaPicksSection() {
  const [picks, setPicks] = useState<FeaturedPick[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchGudaPicks()
      .then((p) => !cancelled && setPicks(p))
      .catch(() => !cancelled && setPicks([]));
    return () => {
      cancelled = true;
    };
  }, []);

  if (!picks || picks.length === 0) return null;

  return (
    <>
      <div className="section-header">
        <h2>Guda Picks</h2>
        <span className="meta">Outcomes our team is watching this week</span>
      </div>
      <div className="grid">
        {picks.map((pick) => (
          <GudaPickCard key={pick.id} pick={pick} />
        ))}
      </div>
    </>
  );
}
