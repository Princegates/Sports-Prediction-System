import { useState } from "react";
import { copyToClipboard } from "../lib/copySelections";

interface Props {
  text: string;
  label?: string;
  className?: string;
}

/** Copies plain text to the clipboard with transient feedback. Stops
 * propagation so it can sit inside a clickable row/card without also
 * triggering that row's own navigation. */
export function CopyButton({ text, label = "Copy", className = "btn ghost" }: Props) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    const ok = await copyToClipboard(text);
    setState(ok ? "copied" : "failed");
    setTimeout(() => setState("idle"), 1500);
  }

  return (
    <button type="button" className={className} onClick={handleClick}>
      {state === "copied" ? "Copied!" : state === "failed" ? "Couldn't copy" : label}
    </button>
  );
}
