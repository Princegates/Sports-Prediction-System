import { useState } from "react";
import type { AdminPick } from "../types";
import { useTilt } from "../lib/useTilt";
import { CopyButton } from "./CopyButton";
import { formatWhatsapp, whatsappLink } from "../lib/whatsapp";

const RISK_LABELS: Record<AdminPick["risk_tier"], string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
};

// Legs shown before the list is collapsed behind a "Show all" toggle -- a
// slip can carry up to 30 (MAX_ADMIN_PICK_LEGS on the backend), which is
// unreadable as one uninterrupted block.
const COLLAPSED_LEG_COUNT = 4;

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** A whole multi-leg slip, not a single outcome -- see GudaPickCard for the
 * single-pick equivalent. Not clickable, same reasoning as GudaPickCard: a
 * leg's own match may still be premium-gated, and the combined number is
 * what's being shown off here, not a doorway into any one leg's page.
 *
 * ``whatsapp`` is only needed for the "a code exists, subscribe to see it"
 * teaser (``pick.has_booking_code`` true but ``pick.booking_code`` still
 * null -- a free-tier viewer); a premium viewer or superadmin gets the real
 * code straight from the API and never renders that branch.
 *
 * ``riskOverride`` replaces the tag ``pick.risk_tier`` would otherwise show.
 * WeeklyPicksSection sets it: ``risk_tier`` is combined-*probability*-based
 * (see betcode.selection.risk_tier) and calibrated for a variable-length AI
 * Generation slip, so a fixed 10-leg accumulator built to a Low combined-
 * *odds* target (5-10) still stacks enough legs to land at a real
 * combined-probability "high" by that unrelated scale -- showing it would
 * contradict the section's own Low/Medium/High odds-band labeling right
 * next to it. */
export function AdminPickCard({
  pick, whatsapp, riskOverride,
}: {
  pick: AdminPick;
  whatsapp?: string | null;
  riskOverride?: AdminPick["risk_tier"];
}) {
  const tilt = useTilt<HTMLDivElement>();
  const [expanded, setExpanded] = useState(false);

  const hiddenCount = pick.legs.length - COLLAPSED_LEG_COUNT;
  const visibleLegs = expanded || hiddenCount <= 0 ? pick.legs : pick.legs.slice(0, COLLAPSED_LEG_COUNT);
  const riskTier = riskOverride ?? pick.risk_tier;

  return (
    <div className="card match-card tilt-card" ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}>
      <div className="match-card-top">
        <span className="match-competition">
          {pick.label || `${pick.legs.length}-leg slip`}
        </span>
        <span className={`risk-tag ${riskTier}`}>{RISK_LABELS[riskTier]}</span>
      </div>

      <ol style={{ display: "flex", flexDirection: "column", gap: 8, margin: "8px 0", padding: 0, listStyle: "none" }}>
        {visibleLegs.map((leg, i) => (
          <li key={`${leg.match_id}-${leg.market}-${leg.selection}`} style={{ display: "flex", gap: 8 }}>
            <span className="tabular-nums" style={{ color: "var(--text-muted)", fontSize: 13 }}>
              {i + 1}.
            </span>
            <span>
              {leg.home_team} vs {leg.away_team}
              <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
                {leg.market}: {leg.selection} · {formatKickoff(leg.kickoff)}
              </div>
            </span>
          </li>
        ))}
      </ol>

      {hiddenCount > 0 && (
        <button
          type="button"
          className="btn ghost"
          style={{ padding: "2px 8px", fontSize: 12, marginBottom: 8 }}
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? "Show fewer" : `Show all ${pick.legs.length} legs (${hiddenCount} more)`}
        </button>
      )}

      <div className="match-meta-row tabular-nums" style={{ fontWeight: 600 }}>
        {pick.priced && pick.combined_odds !== null ? `${pick.combined_odds.toFixed(2)} combined odds · ` : ""}
        {(pick.combined_probability * 100).toFixed(0)}% combined probability
      </div>

      {pick.booking_code && pick.booking_code_bookmaker && (
        <div
          className="match-meta-row"
          style={{ marginTop: 8, padding: "8px 10px", borderRadius: 8, background: "var(--bg-surface-2)", justifyContent: "space-between" }}
        >
          <span>
            <strong>{pick.booking_code_bookmaker}</strong> booking code:{" "}
            <code style={{ fontWeight: 700, letterSpacing: 0.5 }}>{pick.booking_code}</code>
          </span>
          <CopyButton text={pick.booking_code} label="Copy code" />
        </div>
      )}

      {pick.has_booking_code && !pick.booking_code && (
        <div
          className="match-meta-row"
          style={{ marginTop: 8, padding: "8px 10px", borderRadius: 8, background: "var(--bg-surface-2)", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}
        >
          <span>🔒 A booking code is available for this slip -- premium members only.</span>
          {whatsapp && (
            <a
              className="btn ghost"
              style={{ padding: "2px 8px", fontSize: 12 }}
              href={whatsappLink(whatsapp, "Hi, I'd like an access code for Socca Intelligence.")}
              target="_blank"
              rel="noreferrer"
            >
              Message {formatWhatsapp(whatsapp)} on WhatsApp
            </a>
          )}
        </div>
      )}

      {pick.note && (
        <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--text-secondary)", fontStyle: "italic" }}>
          &ldquo;{pick.note}&rdquo;
        </p>
      )}
    </div>
  );
}
