import { useEffect, useState } from "react";
import { fetchGudaPicks, setFeaturedPickResult } from "../api";
import { useAuth } from "../lib/AuthContext";
import { GudaPickCard } from "./GudaPickCard";
import type { FeaturedPick } from "../types";

/** Renders nothing rather than an empty-state -- unlike the free/premium
 * sections above it, there being no picks right now (or the operator having
 * turned the whole thing off) is a normal, unremarkable state, not
 * something worth a "nothing here yet" card.
 *
 * ``onLoaded`` fires after every fetch (success or failure, so a broken
 * section never hangs the caller's own loading state) -- AIPicks uses it to
 * know when all four picks sections have had their first real answer. */
export function GudaPicksSection({ onLoaded }: { onLoaded?: () => void } = {}) {
  const { user } = useAuth();
  const [picks, setPicks] = useState<FeaturedPick[] | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    function load() {
      fetchGudaPicks()
        .then((p) => !cancelled && setPicks(p))
        .catch(() => !cancelled && setPicks([]))
        .finally(() => !cancelled && onLoaded?.());
    }

    load();
    // Re-fetch periodically so a pick drops off as soon as its match
    // finishes, not only on the next page load.
    const interval = setInterval(load, 60_000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  async function handleSetResult(pickId: number, result: "won" | "lost") {
    setBusyId(pickId);
    try {
      const updated = await setFeaturedPickResult(pickId, result);
      setPicks((prev) => (prev ? prev.map((p) => (p.id === pickId ? updated : p)) : prev));
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setBusyId(null);
    }
  }

  if (!picks || picks.length === 0) return null;

  return (
    <div className="section-reveal">
      <div className="section-header">
        <h2>Guda Picks</h2>
        <span className="meta">Outcomes our team is watching this week</span>
      </div>
      <div className="grid">
        {picks.map((pick) => (
          <GudaPickCard
            key={pick.id}
            pick={pick}
            onSetResult={user?.role === "superadmin" ? handleSetResult : undefined}
            busy={busyId === pick.id}
          />
        ))}
      </div>
    </div>
  );
}
