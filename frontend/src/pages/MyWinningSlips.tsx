import { useEffect, useState } from "react";
import { deleteMyWinningSlip, fetchMyWinningSlips, shareWinningSlip } from "../api";
import { ErrorState } from "../components/ErrorState";
import { WinningSlipForm } from "../components/WinningSlipForm";
import { WinningSlipThumb } from "../components/WinningSlipThumb";
import { useAuth } from "../lib/AuthContext";
import type { WinningSlip } from "../types";

const STATUS_LABEL: Record<WinningSlip["status"], string> = {
  live: "Live on the site",
  pending: "Waiting for review",
  not_shown: "Not shown",
};

/**
 * Share a win: any member posts a won SportyBet slip (screenshot or ticket
 * PDF, converted in the browser). It waits for an admin to approve it before
 * it shows on the home page, Dashboard and AI Picks; the member sees each
 * one's status here and can withdraw it any time.
 */
export function MyWinningSlips() {
  const { user } = useAuth();
  const [slips, setSlips] = useState<WinningSlip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [justShared, setJustShared] = useState(false);
  const isAdmin = user?.role === "superadmin";

  function load() {
    setError(null);
    fetchMyWinningSlips()
      .then(setSlips)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }

  useEffect(load, []);

  async function withdraw(id: number) {
    if (!window.confirm("Remove this winning slip? It comes off the site too if it's live.")) return;
    try {
      await deleteMyWinningSlip(id);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (error && slips === null) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="section-header">
        <h2>Share a win</h2>
        <span className="meta">Won with our picks? Show everyone.</span>
      </div>
      <p className="setting-note" style={{ maxWidth: 620 }}>
        Post your won SportyBet slip -- a screenshot, or the ticket saved as a PDF (we tidy it up and remove your
        account link and IP address automatically).{" "}
        {isAdmin
          ? "As an admin, yours go live straight away."
          : "Our team checks each one before it appears on the site, usually within a day."}
      </p>

      {justShared && (
        <p className="wins-admin-conversion" role="status" style={{ maxWidth: 520 }}>
          {isAdmin ? "Posted -- it's live now." : "Thanks! It's waiting for review -- you'll see its status below."}
        </p>
      )}

      <WinningSlipForm
        submitLabel="Share my win"
        busyLabel="Sharing…"
        onSubmit={(body) =>
          shareWinningSlip(body).then(() => {
            setJustShared(true);
            load();
          })
        }
      />

      {error && <p className="auth-error">{error}</p>}

      {slips && slips.length > 0 && (
        <>
          <h3 style={{ marginTop: 24 }}>Your shared slips</h3>
          <div className="wins-admin-list">
            {slips.map((s) => (
              <div key={s.id} className="card wins-admin-item">
                <WinningSlipThumb slip={s} asAdmin={false} />
                <div className="wins-admin-meta">
                  <strong>{s.caption || "No caption"}</strong>
                  <span className={`meta wins-status ${s.status}`}>{STATUS_LABEL[s.status]}</span>
                  <span className="meta">Shared {new Date(s.created_at).toLocaleDateString()}</span>
                  <div className="admin-actions">
                    <button className="btn ghost btn-sm admin-delete-btn" onClick={() => withdraw(s.id)}>
                      Remove
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
