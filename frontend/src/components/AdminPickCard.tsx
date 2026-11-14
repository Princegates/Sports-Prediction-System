import { useState } from "react";
import { Link } from "react-router-dom";
import type { AdminPick } from "../types";
import { useTilt } from "../lib/useTilt";
import { CopyButton } from "./CopyButton";
import { LedgerRow } from "./LedgerRow";
import { whatsappLink } from "../lib/whatsapp";
import { chanceLabel } from "../lib/odds";
import { RISK_LABELS, systemSlipTitle } from "../lib/risk";

const RESULT_LABELS: Record<"won" | "lost" | "unresolved", string> = {
  won: "Won",
  lost: "Lost",
  unresolved: "Confirming result",
};

// Legs shown before the list is collapsed behind a "Show all" toggle -- a
// slip can carry up to 30 (MAX_ADMIN_PICK_LEGS on the backend), which is
// unreadable as one uninterrupted block.
const COLLAPSED_LEG_COUNT = 4;

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
 * The risk tag always goes by the slip's combined probability
 * (``pick.risk_tier``, see lib/risk). The system daily/weekly accumulators
 * are titled by how likely each game on them is ("85%+ games"), never as
 * "Low risk": ten games at 85% each still only all win about 1 time in 5.
 *
 * ``hideRisk`` drops the tag entirely -- RandomPicksSection sets it: a slip
 * drawn at random, deliberately not ranked or filtered by risk level, would
 * otherwise show "High risk" on every single one (a 10-15 leg combined
 * probability is small almost by construction), implying a ranking that was
 * never applied. */
export function AdminPickCard({
  pick, whatsapp, hideRisk,
}: {
  pick: AdminPick;
  whatsapp?: string | null;
  hideRisk?: boolean;
}) {
  const tilt = useTilt<HTMLDivElement>();
  const [expanded, setExpanded] = useState(false);

  const hiddenCount = pick.legs.length - COLLAPSED_LEG_COUNT;
  const visibleLegs = expanded || hiddenCount <= 0 ? pick.legs : pick.legs.slice(0, COLLAPSED_LEG_COUNT);
  const riskTier = pick.risk_tier;
  const title = systemSlipTitle(pick) ?? pick.label;

  if (pick.locked) {
    return (
      <div className="card match-card match-card-static tilt-card" ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}>
        <div className="match-card-top">
          <span className="match-competition">{title || `${pick.leg_count}-leg slip`}</span>
          {!hideRisk && (
            <span className={`risk-tag ${riskTier}`}>
              <span aria-hidden>◆</span>
              {RISK_LABELS[riskTier]}
            </span>
          )}
        </div>
        <div style={{ textAlign: "center", padding: "20px 0 4px" }}>
          <div style={{ fontSize: 26 }} aria-hidden>🔒</div>
          <p style={{ margin: "6px 0 10px", fontSize: 13, color: "var(--text-secondary)" }}>
            This {pick.leg_count}-leg slip is premium members only.
          </p>
          <Link className="btn" style={{ padding: "4px 12px", fontSize: 12.5 }} to="/app/access">
            Redeem a code to unlock
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div
      className="card match-card match-card-static tilt-card"
      ref={tilt.ref}
      onMouseMove={tilt.onMouseMove}
      onMouseLeave={tilt.onMouseLeave}
    >
      <div className="match-card-top">
        <span className="match-competition">
          {title || `${pick.legs.length}-leg slip`}
        </span>
        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {pick.result !== "pending" && (
            <span className={`result-tag ${pick.result}`}>{RESULT_LABELS[pick.result]}</span>
          )}
          {!hideRisk && pick.result === "pending" && (
            <span className={`risk-tag ${riskTier}`}>
              <span aria-hidden>◆</span>
              {RISK_LABELS[riskTier]}
            </span>
          )}
        </span>
      </div>

      <ol style={{ margin: "8px 0", padding: 0 }}>
        {visibleLegs.map((leg, i) => (
          <LedgerRow key={`${leg.match_id}-${leg.market}-${leg.selection}`} leg={leg} index={i + 1} />
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
        {(pick.combined_probability * 100).toFixed(0)}% combined probability (lands {chanceLabel(pick.combined_probability)})
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
              Contact admin on WhatsApp
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
