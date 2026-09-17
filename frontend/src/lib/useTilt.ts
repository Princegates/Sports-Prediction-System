import { useCallback, useRef, type MouseEvent } from "react";

const MAX_TILT_DEG = 6;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Subtle pointer-driven 3D tilt for cards -- a depth micro-interaction, not a gimmick. */
export function useTilt<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);

  const onMouseMove = useCallback((e: React.MouseEvent<T>) => {
    const el = ref.current;
    if (!el || prefersReducedMotion()) return;
    const rect = el.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width - 0.5;
    const py = (e.clientY - rect.top) / rect.height - 0.5;
    el.style.transform = `perspective(800px) rotateX(${(-py * MAX_TILT_DEG).toFixed(2)}deg) rotateY(${(px * MAX_TILT_DEG).toFixed(2)}deg) translateZ(0)`;
    el.style.setProperty("--glare-x", `${(px + 0.5) * 100}%`);
    el.style.setProperty("--glare-y", `${(py + 0.5) * 100}%`);
  }, []);

  const onMouseLeave = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.transform = "perspective(800px) rotateX(0deg) rotateY(0deg) translateZ(0)";
  }, []);

  return { ref, onMouseMove, onMouseLeave };
}
