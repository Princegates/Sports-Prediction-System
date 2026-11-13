import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { fetchSharedSelection } from "../api";
import type { SharedSelection } from "../types";
import { ErrorState } from "../components/ErrorState";

/**
 * The page a share link opens. Shows what was shared, then "Edit and make my
 * code" hands the games still to play to AI Generation, where they can be
 * changed (remove games, refresh, add your own) before the betting-site buttons
 * make a code. It is a link to a page, not to a query string, so signing in first
 * brings the person straight back here.
 */

function when(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function SharedSlip() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const [shared, setShared] = useState<SharedSelection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSharedSelection(token)
      .then((s) => !cancelled && setShared(s))
      .catch((e) => !cancelled && setError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (error) {
    return (
      <div>
        <ErrorState message={error} />
        <p className="setting-note">
          Ask your friend to share the slip again, or <Link to="/app/betcodes">make your own in AI Generation</Link>.
        </p>
      </div>
    );
  }
  if (!shared) return <p className="sub">Opening the shared slip…</p>;

  const total = shared.games.length;
  const allGone = shared.upcoming_count === 0;

  function handleEdit() {
    if (!shared) return;
    const picks = shared.games
      .filter((g) => g.upcoming)
      .map((g) => ({ match_id: g.match_id, market: g.market, selection: g.selection }));
    navigate("/app/betcodes", { state: { picks, shared: { name: shared.name, by: shared.shared_by } } });
  }

  return (
    <div>
      <div className="section-header">
        <h2>A slip shared with you</h2>
        <span className="meta">
          {shared.shared_by ? `${shared.shared_by} shared this` : "A member shared this"}. Look it over, change
          anything you like, then make your own code. Your changes never touch theirs.
        </span>
      </div>

      <div className="card card-pad">
        <h3 style={{ marginTop: 0 }}>
          {shared.name}{" "}
          <span className="sub">
            · {total} game{total === 1 ? "" : "s"}
          </span>
        </h3>
        {!allGone && shared.upcoming_count < total && (
          <p className="setting-note" style={{ color: "var(--warning)" }}>
            {shared.upcoming_count} of {total} games are still to play. Only those come with you.
          </p>
        )}
        {allGone && (
          <p className="setting-note">
            All of these games have started or finished, so there is nothing left to edit.
          </p>
        )}
        <ul className="doc-list">
          {shared.games.map((g) => (
            <li key={`${g.match_id}|${g.market}|${g.selection}`} style={{ opacity: g.upcoming ? 1 : 0.55 }}>
              <strong>
                {g.home_team}
                {g.away_team ? ` vs ${g.away_team}` : ""}
              </strong>{" "}
              -- {g.market}: {g.selection}
              {g.kickoff && <span className="sub"> · {when(g.kickoff)}</span>}
              {!g.upcoming && <span className="sub"> · started or finished</span>}
            </li>
          ))}
        </ul>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
          <button type="button" className="btn" disabled={allGone} onClick={handleEdit}>
            Edit and make my code
          </button>
          <Link className="btn ghost" to="/app/betcodes">
            Make my own instead
          </Link>
        </div>
      </div>
    </div>
  );
}
