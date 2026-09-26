import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

const STORAGE_KEY = "chat_fab_pos";
const DRAG_THRESHOLD = 6; // px of pointer movement before a press counts as a drag, not a tap
const EDGE_MARGIN = 10;

interface Pos {
  x: number;
  y: number;
}

interface DragState {
  startX: number;
  startY: number;
  originX: number;
  originY: number;
  dragged: boolean;
}

function readStoredPos(): Pos | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.x === "number" && typeof parsed?.y === "number") return parsed;
  } catch {
    // storage disabled or corrupt -- fall back to the default corner
  }
  return null;
}

function clamp(pos: Pos, el: HTMLElement): Pos {
  const { width, height } = el.getBoundingClientRect();
  const maxX = Math.max(EDGE_MARGIN, window.innerWidth - width - EDGE_MARGIN);
  const maxY = Math.max(EDGE_MARGIN, window.innerHeight - height - EDGE_MARGIN);
  return {
    x: Math.min(Math.max(pos.x, EDGE_MARGIN), maxX),
    y: Math.min(Math.max(pos.y, EDGE_MARGIN), maxY),
  };
}

/**
 * Makes the floating Guda launcher draggable anywhere on screen and
 * remembers where the user left it. A press that never moves past the drag
 * threshold still fires `onActivate` (opening the chat dock), so dragging
 * never breaks the ordinary tap-to-open interaction.
 */
export function useDraggableFab(onActivate: () => void) {
  const ref = useRef<HTMLButtonElement | null>(null);
  const [pos, setPos] = useState<Pos | null>(null);
  const drag = useRef<DragState | null>(null);

  useEffect(() => {
    const stored = readStoredPos();
    if (stored && ref.current) setPos(clamp(stored, ref.current));
  }, []);

  useEffect(() => {
    function onResize() {
      setPos((p) => (p && ref.current ? clamp(p, ref.current) : p));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const onPointerDown = useCallback((e: ReactPointerEvent<HTMLButtonElement>) => {
    const el = ref.current;
    if (!el || e.button !== 0) return;
    const rect = el.getBoundingClientRect();
    el.setPointerCapture(e.pointerId);
    drag.current = { startX: e.clientX, startY: e.clientY, originX: rect.left, originY: rect.top, dragged: false };
  }, []);

  const onPointerMove = useCallback((e: ReactPointerEvent<HTMLButtonElement>) => {
    const state = drag.current;
    const el = ref.current;
    if (!state || !el) return;
    const dx = e.clientX - state.startX;
    const dy = e.clientY - state.startY;
    if (!state.dragged && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    state.dragged = true;
    setPos(clamp({ x: state.originX + dx, y: state.originY + dy }, el));
  }, []);

  const onPointerUp = useCallback(
    (e: ReactPointerEvent<HTMLButtonElement>) => {
      const state = drag.current;
      drag.current = null;
      if (!state) return;
      ref.current?.releasePointerCapture(e.pointerId);
      if (!state.dragged) {
        onActivate();
        return;
      }
      setPos((p) => {
        try {
          if (p) localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
        } catch {
          // storage disabled -- the position just won't persist across reloads
        }
        return p;
      });
    },
    [onActivate],
  );

  const style = pos ? { left: pos.x, top: pos.y, right: "auto", bottom: "auto" } : undefined;

  return { ref, style, onPointerDown, onPointerMove, onPointerUp };
}
