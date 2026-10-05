import { AdminPicksSection } from "../components/AdminPicksSection";
import { GudaPicksSection } from "../components/GudaPicksSection";
import { RandomPicksSection } from "../components/RandomPicksSection";
import { WeeklyPicksSection } from "../components/WeeklyPicksSection";

/** Every featured-slip section in one place, curated-to-random: Guda Picks
 * and Admin Picks are human-curated; This Week's Picks and Random Picks are
 * cron-generated with no human in the loop. Previously split across the
 * Dashboard (these four sections) and this page didn't exist -- moved here
 * so the Dashboard is just the hero and Match Discovery, and picks have
 * their own home in the nav. Free for any logged-in account, same as the
 * Dashboard -- see App.tsx's routing comment for why. */
export function AIPicks() {
  return (
    <div>
      <div className="section-header">
        <h2>AI Picks</h2>
        <span className="meta">Every featured slip Guda and the system have put together, in one place</span>
      </div>

      <GudaPicksSection />
      <AdminPicksSection />
      <WeeklyPicksSection />
      <RandomPicksSection />
    </div>
  );
}
