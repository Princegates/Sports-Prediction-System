import { useEffect, useState } from "react";
import { deleteWinningSlip, fetchAdminWinningSlips, postWinningSlip, updateWinningSlip } from "../api";
import { ErrorState } from "../components/ErrorState";
import { WinningSlipForm } from "../components/WinningSlipForm";
import { WinningSlipThumb } from "../components/WinningSlipThumb";
import type { AdminWinningSlip } from "../types";

const STATUS_LABEL: Record<AdminWinningSlip["status"], string> = {
  live: "Live",
  pending: "Waiting for review",
  not_shown: "Hidden",
};

/**
 * Admin -> Winning slips. Post the team's own winning slips (live straight
 * away), and review the ones members share from Share a win: Approve puts
 * one live on the home page, Dashboard and AI Picks; Decline keeps it off.
 */
export function AdminWinningSlips() {
  const [slips, setSlips] = useState<AdminWinningSlip[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setError(null);
    fetchAdminWinningSlips()
      .then(setSlips)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }

  useEffect(load, []);

  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (error && slips === null) return <ErrorState message={error} onRetry={load} />;

  const pending = slips?.filter((s) => s.status === "pending") ?? [];
  const others = slips?.filter((s) => s.status !== "pending") ?? [];

  function item(s: AdminWinningSlip) {
    return (
      <div key={s.id} className={`card wins-admin-item${s.status === "live" ? "" : " hidden-slip"}`}>
        <WinningSlipThumb slip={s} asAdmin />
        <div className="wins-admin-meta">
          <strong>{s.caption || "No caption"}</strong>
          <span className="meta">
            {STATUS_LABEL[s.status]}
            {s.won_on ? ` · won ${s.won_on}` : ""} · {new Date(s.created_at).toLocaleDateString()}
          </span>
          <span className="meta">
            {s.from_member ? `Shared by ${s.submitted_by_name ?? "a member"}${s.submitted_by_email ? ` (${s.submitted_by_email})` : ""}` : "Posted by the team"}
          </span>
          <div className="admin-actions">
            {s.status === "pending" ? (
              <>
                <button className="btn btn-sm" onClick={() => act(() => updateWinningSlip(s.id, { published: true }))}>
                  Approve
                </button>
                <button className="btn ghost btn-sm" onClick={() => act(() => updateWinningSlip(s.id, { published: false }))}>
                  Decline
                </button>
              </>
            ) : (
              <button className="btn ghost btn-sm" onClick={() => act(() => updateWinningSlip(s.id, { published: !s.published }))}>
                {s.published ? "Hide" : "Show"}
              </button>
            )}
            <button
              className="btn ghost btn-sm admin-delete-btn"
              onClick={() => window.confirm("Delete this winning slip for good?") && act(() => deleteWinningSlip(s.id))}
            >
              Delete
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="admin-header">
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>Winning slips</h1>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0" }}>
            Won SportyBet slips on the public home page, every member's Dashboard and AI Picks. Yours go live as soon
            as you post them; members' wait for your review below.
          </p>
        </div>
      </div>

      {error && <p className="auth-error" style={{ marginTop: 12 }}>{error}</p>}

      {pending.length > 0 && (
        <section className="wins-review">
          <h2 style={{ fontSize: 17 }}>Waiting for review ({pending.length})</h2>
          <p className="setting-note">
            Check it's a real won ticket and that nothing private is showing (balance, phone number, user ID) before
            approving.
          </p>
          <div className="wins-admin-list">{pending.map(item)}</div>
        </section>
      )}

      <WinningSlipForm submitLabel="Post winning slip" onSubmit={(body) => postWinningSlip(body).then(load)} />

      <div className="wins-admin-list">
        {slips !== null && others.length === 0 && <p className="meta">Nothing posted yet.</p>}
        {others.map(item)}
      </div>
    </div>
  );
}
