import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 640px)";

/** Same breakpoint as styles.css's phone rules (bottom nav, card tables) --
 * for the rare case a component needs to branch its own JSX by viewport
 * rather than just its CSS, such as swapping a long form for a step
 * wizard. Re-evaluates on resize/rotation, not just on mount. */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  return isMobile;
}
