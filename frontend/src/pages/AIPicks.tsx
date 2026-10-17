import { useCallback, useEffect, useState } from "react";
import { fetchAdminPicks } from "../api";
import { AdminPicksSection } from "../components/AdminPicksSection";
import { CardGridSkeleton } from "../components/LoadingSkeleton";
import { GudaPicksSection } from "../components/GudaPicksSection";
import { RandomPicksSection } from "../components/RandomPicksSection";
import { WeeklyPicksSection } from "../components/WeeklyPicksSection";
import type { AdminPick } from "../types";

const SECTIONS = ["guda", "adminPicks"] as const;
type Section = (typeof SECTIONS)[number];

/** Every featured-slip section in one place, curated-to-random: Guda Picks
 * and Admin Picks are human-curated; This Week's Picks and Random Picks are
 * cron-generated with no human in the loop. Previously split across the
 * Dashboard (these four sections) and this page didn't exist -- moved here
 * so the Dashboard is just the hero and Match Discovery, and picks have
 * their own home in the nav. Free for any logged-in account, same as the
 * Dashboard -- see App.tsx's routing comment for why.
 *
 * Admin Picks, This Week's Picks and Random Picks are three different
 * slices of the exact same GET /api/predictions/admin-picks response
 * (AdminPickCard.source tells them apart client-side, see each section's
 * own filter) -- fetched once here and handed down as a prop, rather than
 * each of those three sections independently re-requesting it. They used
 * to each fetch on their own; the backend re-settles and re-prices every
 * pick on every call to that endpoint, so three independent fetches meant
 * tripling that work for identical data on every single page load.
 *
 * Guda Picks hits its own, separate endpoint, so it keeps its own fetch
 * and ``onLoaded`` callback. The shared skeleton covers both sources at
 * once and clears the moment each has had its first real answer -- the
 * sections stay mounted underneath the whole time (display:none, not
 * unmounted) so their fetches aren't delayed by this wrapper at all. */
export function AIPicks() {
  const [loaded, setLoaded] = useState<Set<Section>>(new Set());
  const markLoaded = useCallback((section: Section) => {
    setLoaded((prev) => (prev.has(section) ? prev : new Set(prev).add(section)));
  }, []);
  const stillLoading = loaded.size < SECTIONS.length;

  const [adminPicks, setAdminPicks] = useState<AdminPick[] | null>(null);

  useEffect(() => {
    let cancelled = false;

    function load() {
      fetchAdminPicks()
        .then((p) => !cancelled && setAdminPicks(p))
        .catch(() => !cancelled && setAdminPicks([]))
        .finally(() => !cancelled && markLoaded("adminPicks"));
    }

    load();
    // Re-fetch periodically so a slip drops off (or loses a leg, or
    // settles) the moment one of its matches finishes, not only on the
    // next page load -- same cadence the three sections used individually.
    const interval = setInterval(load, 60_000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [markLoaded]);

  return (
    <div>
      <div className="section-header">
        <h2>AI Picks</h2>
        <span className="meta">Every featured slip Guda and the system have put together, in one place</span>
      </div>

      {stillLoading && (
        <>
          <p className="meta" role="status" style={{ marginBottom: 10 }}>
            Loading your AI picks…
          </p>
          <CardGridSkeleton />
        </>
      )}

      <div style={{ display: stillLoading ? "none" : undefined }}>
        <GudaPicksSection onLoaded={() => markLoaded("guda")} />
        <AdminPicksSection picks={adminPicks} />
        <WeeklyPicksSection picks={adminPicks} />
        <RandomPicksSection picks={adminPicks} />
      </div>
    </div>
  );
}
