import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  TOUR_STEPS,
  OPEN_TOUR_EVENT,
  hasOptedOutOfTour,
  markTourShownThisVisit,
  setTourOptOut,
  shouldAutoShowTour,
  type TourStep,
} from "../lib/tour";
import { useAuth } from "../lib/AuthContext";
import { AiGenerate, ChartLine, Close, Home, Live, Locked, PinFilled, Search, SettingsIcon, ShoppingCatalog, Trophy, UserAvatar } from "./icons";
import { Mascot } from "./Mascot";

const ICONS: Record<TourStep["icon"], (p: { size?: number }) => JSX.Element> = {
  mascot: () => <Mascot pose="celebrating" size={48} />,
  home: (p) => <Home {...p} />,
  league: (p) => <PinFilled {...p} />,
  search: (p) => <Search {...p} />,
  chart: (p) => <ChartLine {...p} />,
  markets: (p) => <ShoppingCatalog {...p} />,
  picks: (p) => <PinFilled {...p} />,
  generate: (p) => <AiGenerate {...p} />,
  live: (p) => <Live {...p} />,
  share: (p) => <Trophy {...p} />,
  access: (p) => <Locked {...p} />,
  theme: (p) => <SettingsIcon {...p} />,
  menu: (p) => <UserAvatar {...p} />,
};

interface Spot {
  left: number;
  top: number;
  width: number;
  height: number;
  radius: number;
}

const PAD = 6; // breathing room around the lit-up button
const POINTER_ROOM = 78; // space kept between the button and the card for the pointing hand

/** The first button named in `keys` that is actually on screen -- a phone
 * shows the bottom bar and hides the top tabs (and the other way round). */
function findTarget(keys: string[] | undefined): HTMLElement | null {
  for (const key of keys ?? []) {
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(`[data-tour="${key}"]`))) {
      const r = el.getBoundingClientRect();
      const onScreen = r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
      if (onScreen) return el;
    }
  }
  return null;
}

function measure(el: HTMLElement): Spot {
  const r = el.getBoundingClientRect();
  const corner = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
  const height = r.height + PAD * 2;
  return {
    left: r.left - PAD,
    top: r.top - PAD,
    width: r.width + PAD * 2,
    height,
    radius: Math.min(corner + PAD, height / 2),
  };
}

function sameSpot(a: Spot | null, b: Spot | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (["left", "top", "width", "height", "radius"] as const).every((k) => Math.abs(a[k] - b[k]) < 0.5);
}

/** A pointing hand. The fingertip is the top-centre of the drawing. */
function PointerHand() {
  return (
    <svg viewBox="0 0 34 44" width="34" height="44" aria-hidden>
      <path
        d="M13 3.5a3.5 3.5 0 017 0V19h2.2a3.3 3.3 0 013.3 3.3v.7a3.3 3.3 0 013 3.3v1a3.3 3.3 0 013 3.3v6.4c0 4.6-3.7 8.3-8.3 8.3h-6.2c-3 0-4.9-1.3-6.6-3.7L4 31.4a3.2 3.2 0 015.2-3.7L13 32V3.5z"
        fill="#fff"
        stroke="#1b1b1f"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Walkthrough of the platform. Opens by itself each time a member opens the
 * app until they tick "Don't show this again" (see lib/tour.shouldAutoShowTour),
 * and on demand from the account menu.
 *
 * A dialog rather than spotlight bubbles over the real page on purpose: the
 * navigation is a top bar on desktop and a bottom bar on phones, and
 * anchoring bubbles to elements that move between layouts is exactly what
 * makes tours break.
 */
export function TourDialog() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [spot, setSpot] = useState<Spot | null>(null);
  const [viewport, setViewport] = useState({ w: window.innerWidth, h: window.innerHeight });
  const [index, setIndex] = useState(0);
  const [dontShow, setDontShow] = useState(false);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const decided = useRef<number | null>(null);

  // Opens itself once, a moment after the page settles, for a new account.
  // `decided` is only set once the answer is final (not eligible, or already
  // shown): if a re-render cancels the timer first, the next run reschedules
  // it instead of silently never showing the tour.
  useEffect(() => {
    if (!user || decided.current === user.id) return;
    if (!shouldAutoShowTour(user)) {
      decided.current = user.id;
      return;
    }
    const timer = window.setTimeout(() => {
      decided.current = user.id;
      markTourShownThisVisit(user.id);
      setIndex(0);
      setDontShow(false);
      setOpen(true);
    }, 900);
    return () => window.clearTimeout(timer);
    // Keyed on the account, not the user object: a profile refresh hands back
    // a new object for the same person and must not restart the countdown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  // "Take the tour" in the account menu.
  useEffect(() => {
    function onOpen() {
      setIndex(0);
      setDontShow(user ? hasOptedOutOfTour(user.id) : false);
      setOpen(true);
    }
    window.addEventListener(OPEN_TOUR_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_TOUR_EVENT, onOpen);
  }, [user]);

  const close = useCallback(() => {
    setOpen(false);
    if (user) {
      markTourShownThisVisit(user.id);
      setTourOptOut(user.id, dontShow);
    }
  }, [user, dontShow]);

  const last = TOUR_STEPS.length - 1;
  const next = useCallback(() => setIndex((i) => Math.min(i + 1, last)), [last]);
  const back = useCallback(() => setIndex((i) => Math.max(i - 1, 0)), []);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") next();
      else if (e.key === "ArrowLeft") back();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close, next, back]);

  // Open the page a step is about, so the button it points at exists.
  const stepRoute = open ? TOUR_STEPS[index].route : undefined;
  useEffect(() => {
    if (stepRoute && location.pathname !== stepRoute && location.pathname !== `${stepRoute}/`) navigate(stepRoute);
    // Only when the step changes; a later page change must not drag the tour back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepRoute, open, index]);

  // Follow the real button: it can appear a moment late (a page still
  // loading), move (window resized, phone turned) or not exist at all.
  useEffect(() => {
    if (!open) {
      setSpot(null);
      return;
    }
    const keys = TOUR_STEPS[index].target;
    if (!keys) {
      setSpot(null);
      return;
    }
    function update() {
      const el = findTarget(keys);
      const next = el ? measure(el) : null;
      setSpot((prev) => (sameSpot(prev, next) ? prev : next));
      setViewport((prev) => (prev.w === window.innerWidth && prev.h === window.innerHeight ? prev : { w: window.innerWidth, h: window.innerHeight }));
    }
    update();
    const timer = window.setInterval(update, 200);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, index]);

  // Keyboard focus follows the dialog, so Enter always means "next".
  useEffect(() => {
    if (open) primaryRef.current?.focus();
  }, [open, index]);

  if (!open) return null;

  const step = TOUR_STEPS[index];
  const Icon = ICONS[step.icon];
  const isLast = index === last;
  const found = spot !== null;

  // Put the card on the far side of the lit-up button from the pointer, so the
  // card never covers the thing it is talking about.
  let cardStyle: React.CSSProperties | undefined;
  let pointerBelow = true;
  if (spot) {
    pointerBelow = spot.top + spot.height / 2 < viewport.h / 2;
    cardStyle = pointerBelow
      ? { top: spot.top + spot.height + POINTER_ROOM, maxHeight: `calc(100vh - ${spot.top + spot.height + POINTER_ROOM}px - 12px)` }
      : { bottom: viewport.h - spot.top + POINTER_ROOM, maxHeight: `calc(${spot.top - POINTER_ROOM}px - 12px)` };
  }
  const tryText = found ? step.tryIt : step.tryItElse;

  return (
    <div
      className={`tour-backdrop${found ? " tour-backdrop--spot" : ""}`}
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      {spot && (
        <>
          <div
            className="tour-spot"
            aria-hidden
            style={{ left: spot.left, top: spot.top, width: spot.width, height: spot.height, borderRadius: spot.radius }}
          />
          <span
            className="tour-ripple"
            aria-hidden
            style={{ left: spot.left + spot.width / 2, top: spot.top + spot.height / 2 }}
          />
          <div
            className={`tour-pointer ${pointerBelow ? "tour-pointer--below" : "tour-pointer--above"}`}
            aria-hidden
            style={{
              left: spot.left + spot.width / 2,
              top: pointerBelow ? spot.top + spot.height - 2 : spot.top + 2,
            }}
          >
            <div className="tour-pointer-hand">
              <PointerHand />
            </div>
          </div>
        </>
      )}

      <div
        className={`tour-card${cardStyle ? " tour-card--placed" : ""}`}
        style={cardStyle}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
      >
        <button type="button" className="tour-close" aria-label="Close tour" onClick={close}>
          <Close size={18} />
        </button>

        <div className="tour-scroll">
          <div className="tour-head">
            <div className="tour-icon" aria-hidden>
              <Icon size={26} />
            </div>
            <div>
              <p className="tour-step-count">
                Step {index + 1} of {TOUR_STEPS.length}
              </p>
              <h2 id="tour-title" className="tour-title">
                {step.title}
              </h2>
            </div>
          </div>

          <p className="tour-body">{step.body}</p>

          {tryText && (
            <p className="tour-tryit">
              <strong>Where to tap:</strong> {tryText}
            </p>
          )}

          {step.points && (
            <>
              {step.target && <p className="tour-label">What you can do here</p>}
              <ul className="tour-points">
                {step.points.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="tour-foot">
          <div className="tour-progress" aria-hidden>
            <span style={{ width: `${((index + 1) / TOUR_STEPS.length) * 100}%` }} />
          </div>

          <label className="tour-optout">
            <input type="checkbox" checked={dontShow} onChange={(e) => setDontShow(e.target.checked)} />
            Don&apos;t show this again
          </label>

          <div className="tour-actions">
            {index > 0 ? (
              <button type="button" className="btn ghost" onClick={back}>
                Back
              </button>
            ) : (
              <button type="button" className="btn ghost" onClick={close}>
                Skip tour
              </button>
            )}
            {isLast ? (
              <button type="button" className="btn" ref={primaryRef} onClick={close}>
                Start exploring
              </button>
            ) : (
              <button type="button" className="btn" ref={primaryRef} onClick={next}>
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
