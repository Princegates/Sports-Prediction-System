import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import { chanceLabel } from "../lib/odds";
import { savePendingSlip } from "../lib/pendingSlip";
import { fixtureSlug } from "../lib/slug";
import type { BetCodePick, PublicFreePick } from "../types";
import { TeamCrest } from "./TeamCrest";

const KIND_TAG: Record<PublicFreePick["kind"], string> = {
  "1x2": "1X2",
  double_chance: "Double Chance",
  over_1_5: "Over 1.5",
};

const STATUS_TAG: Record<Exclude<PublicFreePick["status"], "upcoming">, string> = {
  live: "Live",
  won: "Won",
  lost: "Lost",
  void: "Void",
};

function kickoffLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

/**
 * Today's three free picks -- one 1X2, one Double Chance, one Over 1.5 --
 * shown together as one slip, for everyone. "Generate booking code" books
 * the slip on a betting site: straight away for a signed-in member, and for
 * a visitor it keeps the slip and sends them to create an account first.
 */
export function FreePicksSlip({ picks, trialDays = 0 }: { picks: PublicFreePick[]; trialDays?: number }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  if (picks.length === 0) return null;

  const bookable: BetCodePick[] = picks
    .filter((p) => p.status === "upcoming")
    .map((p) => ({ match_id: p.fixture.match_id, market: p.market, selection: p.selection }));
  const combined = picks.reduce((acc, p) => acc * p.probability, 1);
  const allOver = picks.every((p) => p.status === "won" || p.status === "lost" || p.status === "void");

  function generateCode() {
    // Kept for a signed-in account too: one without live access is sent to
    // the Access page first, and the slip is waiting once they're through.
    savePendingSlip(bookable);
    if (user) navigate("/app/betcodes", { state: { picks: bookable } });
    else navigate("/register");
  }

  return (
    <div className="free-slip card card-pad" id="free-picks">
      <div className="potd-head">
        <span className="eyebrow">Today's {picks.length} free picks</span>
        <span className="meta">New picks as soon as these matches finish</span>
      </div>

      <ol className="free-slip-list">
        {picks.map((p) => (
          <li key={p.kind} className="free-slip-row">
            <span className="free-slip-kind">{KIND_TAG[p.kind]}</span>
            <Link to={`/predict/${fixtureSlug(p.fixture)}`} className="free-slip-match">
              <TeamCrest name={p.fixture.home_team} logoUrl={p.fixture.home_logo_url} size={22} />
              <span>
                {p.fixture.home_team} <span className="vs-divider">v</span> {p.fixture.away_team}
              </span>
              <TeamCrest name={p.fixture.away_team} logoUrl={p.fixture.away_logo_url} size={22} />
            </Link>
            <span className="meta free-slip-when">
              {p.fixture.league} · {kickoffLabel(p.fixture.kickoff)}
              {p.home_score != null && p.away_score != null && (
                <>
                  {" "}
                  · {p.home_score}–{p.away_score}
                </>
              )}
            </span>
            <span className="free-slip-call">
              <strong>{p.label}</strong>
              <span className="potd-prob">{Math.round(p.probability * 100)}%</span>
              {p.status !== "upcoming" && (
                <span className={`free-slip-status ${p.status}`}>{STATUS_TAG[p.status]}</span>
              )}
            </span>
          </li>
        ))}
      </ol>

      <p className="meta" style={{ margin: 0 }}>
        Each pick is the model's strongest call in its market kicking off soon. Together they land{" "}
        {chanceLabel(combined)} of the time by the model's own numbers, so the slip can still lose.
      </p>

      <div className="landing-cta-row" style={{ marginTop: 4 }}>
        {bookable.length > 0 ? (
          <button type="button" className="btn" onClick={generateCode}>
            Generate booking code
          </button>
        ) : (
          !allOver && <span className="meta">These matches have kicked off -- booking is closed for this slip.</span>
        )}
        {!user && (
          <Link className="btn ghost" to="/register">
            {trialDays > 0 ? `Start your ${trialDays}-day free trial` : "Create a free account"}
          </Link>
        )}
      </div>
      {!user && bookable.length > 0 && (
        <p className="meta" style={{ margin: 0 }}>
          Booking codes need a free account -- we'll keep this slip ready for you after you sign up.
        </p>
      )}
    </div>
  );
}
