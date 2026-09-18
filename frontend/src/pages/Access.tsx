import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { redeemAccessCode } from "../api";
import { Mascot } from "../components/Mascot";
import { useAuth } from "../lib/AuthContext";

/**
 * Where a logged-in account without a live grant lands -- redirected here by
 * RequireAccess instead of being shown a dead dashboard. Also reachable any
 * time from the nav, so a still-active user can redeem a fresh code before
 * theirs runs out.
 */
export function Access() {
  const { accessStatus, accessLoading, refreshAccessStatus } = useAuth();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    refreshAccessStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await redeemAccessCode(code.trim());
      await refreshAccessStatus();
      navigate("/app", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const hasAccess = accessStatus?.has_access ?? false;

  return (
    <div>
      <div className="section-header">
        <h2>Access</h2>
        <span className="meta">Redeem a code to unlock predictions</span>
      </div>

      <div className="card card-pad" style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap", marginBottom: 24 }}>
        <Mascot pose={hasAccess ? "celebrating" : "thinking"} size={72} />
        <div style={{ flex: 1, minWidth: 220 }}>
          {accessLoading && <p style={{ margin: 0 }}>Checking your access...</p>}
          {!accessLoading && hasAccess && accessStatus?.expires_at && (
            <>
              <h1 style={{ fontSize: 18, margin: 0 }}>Your access is active</h1>
              <p style={{ margin: "6px 0 0", fontSize: 13.5, color: "var(--text-secondary)" }}>
                Valid until {new Date(accessStatus.expires_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}.
                Redeeming another code below extends this rather than replacing it.
              </p>
            </>
          )}
          {!accessLoading && !hasAccess && (
            <>
              <h1 style={{ fontSize: 18, margin: 0 }}>
                {accessStatus?.status === "expired" ? "Your access has expired" : "No active access yet"}
              </h1>
              <p style={{ margin: "6px 0 0", fontSize: 13.5, color: "var(--text-secondary)" }}>
                Predictions, teams and matches stay locked until you redeem a code. A Super Admin issues one
                after confirming your payment outside the platform.
              </p>
            </>
          )}
        </div>
      </div>

      <div className="card card-pad" style={{ maxWidth: 420 }}>
        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            Access code
            <input
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="XXXX-XXXX-XXXX"
              autoComplete="off"
              style={{ textTransform: "uppercase" }}
            />
          </label>

          {error && <p className="auth-error">{error}</p>}

          <button className="btn" type="submit" disabled={busy || !code.trim()} style={{ marginTop: 8 }}>
            {busy ? "Redeeming..." : "Redeem code"}
          </button>
        </form>
      </div>
    </div>
  );
}
