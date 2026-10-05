import { useCallback, useState } from "react";
import { AdminPicksSection } from "../components/AdminPicksSection";
import { CardGridSkeleton } from "../components/LoadingSkeleton";
import { GudaPicksSection } from "../components/GudaPicksSection";
import { RandomPicksSection } from "../components/RandomPicksSection";
import { WeeklyPicksSection } from "../components/WeeklyPicksSection";

const SECTIONS = ["guda", "admin", "weekly", "random"] as const;
type Section = (typeof SECTIONS)[number];

/** Every featured-slip section in one place, curated-to-random: Guda Picks
 * and Admin Picks are human-curated; This Week's Picks and Random Picks are
 * cron-generated with no human in the loop. Previously split across the
 * Dashboard (these four sections) and this page didn't exist -- moved here
 * so the Dashboard is just the hero and Match Discovery, and picks have
 * their own home in the nav. Free for any logged-in account, same as the
 * Dashboard -- see App.tsx's routing comment for why.
 *
 * Each section fetches independently and renders nothing of its own while
 * loading (a bare blank gap would otherwise sit under the header for
 * however long the slowest of the four takes) -- a shared skeleton covers
 * for all four at once here instead, via each section's own ``onLoaded``
 * callback, and clears the moment every section has had its first real
 * answer. The sections stay mounted underneath the whole time (display:none,
 * not unmounted) so their fetches aren't delayed by this wrapper at all. */
export function AIPicks() {
  const [loaded, setLoaded] = useState<Set<Section>>(new Set());
  const markLoaded = useCallback((section: Section) => {
    setLoaded((prev) => (prev.has(section) ? prev : new Set(prev).add(section)));
  }, []);
  const stillLoading = loaded.size < SECTIONS.length;

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
        <AdminPicksSection onLoaded={() => markLoaded("admin")} />
        <WeeklyPicksSection onLoaded={() => markLoaded("weekly")} />
        <RandomPicksSection onLoaded={() => markLoaded("random")} />
      </div>
    </div>
  );
}
