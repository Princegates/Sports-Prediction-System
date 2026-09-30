import { useEffect } from "react";
import { CopyButton } from "./CopyButton";

interface Props {
  summary: string;
  copyText: string;
  onView: () => void;
}

/** A phone-only sticky footer for a slip being built while scrolling a long
 * list of matches (Markets' "My picks", AI Generation's leg preview) --
 * both already show their own full actions (remove a leg, get a booking
 * code, ...) in a card, but that card scrolls out of view the moment the
 * user goes back to pick another match. This stays reachable without
 * requiring a trip back up the page for the two things done most often:
 * a quick copy, or jumping back to that card for everything else. Hidden
 * above the phone breakpoint (styles.css) -- desktop has no such problem,
 * the card is never far from the pointer. */
export function StickySlipBar({ summary, copyText, onView }: Props) {
  // The draggable "Ask Guda" launcher's default resting corner (ChatDock,
  // useDraggableFab) sits right on top of this bar's own buttons at phone
  // widths -- a body class (rather than plumbing this through AppShell)
  // lets styles.css nudge the launcher up out of the way only while a bar
  // like this one is actually mounted, and only if the user hasn't already
  // dragged it somewhere else themselves.
  useEffect(() => {
    document.body.classList.add("has-sticky-slip-bar");
    return () => document.body.classList.remove("has-sticky-slip-bar");
  }, []);

  return (
    <div className="sticky-slip-bar">
      <span className="sticky-slip-summary">{summary}</span>
      <div className="sticky-slip-actions">
        <button type="button" className="btn ghost" onClick={onView}>
          View
        </button>
        <CopyButton text={copyText} label="Copy" />
      </div>
    </div>
  );
}
