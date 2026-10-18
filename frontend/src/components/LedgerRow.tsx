/**
 * One leg of a settled (or settling) slip: fixture, market, and an explicit
 * status pill -- including "Pending" when the leg hasn't finished yet.
 *
 * Replaces three near-identical `<li>` blocks (AdminPickCard, MyCodesHistory,
 * Welcome's track record) that only showed a tiny checkmark/cross/question
 * icon, and nothing at all for a still-pending leg -- which is exactly the
 * ambiguity behind this session's "I won one of the slips but it's not
 * showing won" reports. A combo is LOST the moment any one leg loses, even
 * while its other legs are still unplayed (see pick_settlement.py), so a
 * "lost" slip can still contain legs this row has to mark PENDING, not
 * blank. Status colors only -- see styles.css's note by .risk-tag on never
 * repurposing them.
 */

const RESULT_LABELS: Record<"won" | "lost" | "unresolved" | "pending", string> = {
  won: "Won",
  lost: "Lost",
  unresolved: "Unresolved",
  pending: "Pending",
};

export interface LedgerLegLike {
  match_id: number;
  home_team: string;
  away_team: string;
  market: string;
  selection: string;
  kickoff?: string;
  league?: string;
  result?: "won" | "lost" | "unresolved" | null;
}

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function LedgerRow({ leg, index }: { leg: LedgerLegLike; index?: number }) {
  const status = leg.result ?? "pending";
  const meta = [leg.league, leg.kickoff ? formatKickoff(leg.kickoff) : null].filter(Boolean).join(" · ");

  return (
    <li className="ledger-row">
      {index != null && <span className="ledger-row-index tabular-nums">{index}.</span>}
      <div className="ledger-row-body">
        <div className="ledger-row-top">
          <span className="ledger-row-teams">
            {leg.home_team} vs {leg.away_team}
          </span>
          <span className={`result-tag ${status}`}>{RESULT_LABELS[status]}</span>
        </div>
        <div className="ledger-row-meta">
          {leg.market}: {leg.selection}
          {meta ? ` · ${meta}` : ""}
        </div>
      </div>
    </li>
  );
}
