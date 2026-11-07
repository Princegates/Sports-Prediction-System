import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { AiGenerate, ChartLine, Close, Home, Live, Locked, PinFilled, ShoppingCatalog } from "./icons";
import { Mascot } from "./Mascot";

const ICONS: Record<TourStep["icon"], (p: { size?: number }) => JSX.Element> = {
  mascot: () => <Mascot pose="celebrating" size={72} />,
  guda: () => <Mascot pose="idle" size={72} />,
  home: (p) => <Home {...p} />,
  chart: (p) => <ChartLine {...p} />,
  markets: (p) => <ShoppingCatalog {...p} />,
  picks: (p) => <PinFilled {...p} />,
  generate: (p) => <AiGenerate {...p} />,
  live: (p) => <Live {...p} />,
  access: (p) => <Locked {...p} />,
};

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
  const [open, setOpen] = useState(false);
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

  // Keyboard focus follows the dialog, so Enter always means "next".
  useEffect(() => {
    if (open) primaryRef.current?.focus();
  }, [open, index]);

  if (!open) return null;

  const step = TOUR_STEPS[index];
  const Icon = ICONS[step.icon];
  const isLast = index === last;

  function goThere() {
    if (!step.to) return;
    close();
    navigate(step.to);
  }

  return (
    <div className="tour-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title">
        <button type="button" className="tour-close" aria-label="Close tour" onClick={close}>
          <Close size={18} />
        </button>

        <div className="tour-icon" aria-hidden>
          <Icon size={30} />
        </div>

        <p className="tour-step-count">
          Step {index + 1} of {TOUR_STEPS.length}
        </p>
        <h2 id="tour-title" className="tour-title">
          {step.title}
        </h2>
        <p className="tour-body">{step.body}</p>
        {step.points && (
          <ul className="tour-points">
            {step.points.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}

        <div className="tour-dots" aria-hidden>
          {TOUR_STEPS.map((s, i) => (
            <span key={s.id} className={`tour-dot${i === index ? " active" : i < index ? " done" : ""}`} />
          ))}
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
          {step.to && !isLast && (
            <button type="button" className="btn ghost tour-there" onClick={goThere}>
              {step.toLabel ?? "Take me there"}
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
  );
}
