import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { redeemAccessCode } from "../api";
import { Mascot } from "../components/Mascot";
import { useTilt } from "../lib/useTilt";
import { useAuth } from "../lib/AuthContext";

/**
 * Where a logged-in account without a live grant lands -- redirected here by
 * RequireAccess instead of being shown a dead dashboard. Also reachable any
 * time from the nav, so a still-active user can redeem a fresh code before
 * theirs runs out.
 *
 * The backend returns one of a small set of distinct rejection messages
 * (doesn't exist / revoked / expired / assigned to someone else / already
 * redeemed / fully redeemed) -- classifyError maps those to a tone and icon
 * rather than showing raw API text as an undifferentiated red banner.
 */

type ErrorKind = "invalid" | "expired" | "revoked" | "limit" | "assigned" | "duplicate" | "network" | "other";

function classifyError(message: string): ErrorKind {
  const m = message.toLowerCase();
  if (m.includes("doesn't exist")) return "invalid";
  if (m.includes("fully redeemed")) return "limit";
  if (m.includes("already redeemed")) return "duplicate";
  if (m.includes("assigned")) return "assigned";
  if (m.includes("revoked")) return "revoked";
  if (m.includes("expired")) return "expired";
  if (m.includes("fetch") || m.includes("network") || m.includes("failed to fetch")) return "network";
  return "other";
}

const ERROR_COPY: Record<ErrorKind, { title: string; icon: string }> = {
  invalid: { title: "That code doesn't exist", icon: "✕" },
  expired: { title: "That code has expired", icon: "⧗" },
  revoked: { title: "That code has been revoked", icon: "⊘" },
  limit: { title: "That code is fully redeemed", icon: "▦" },
  assigned: { title: "That code belongs to another account", icon: "⚿" },
  duplicate: { title: "You've already redeemed that code", icon: "↺" },
  network: { title: "Couldn't reach the server", icon: "⚡" },
  other: { title: "That code couldn't be redeemed", icon: "✕" },
};

function formatRemaining(expiresAt: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const days = Math.floor(ms / 86_400_000);
  const hours = Math.floor((ms % 86_400_000) / 3_600_000);
  if (days > 0) return `${days}d ${hours}h remaining`;
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  return hours > 0 ? `${hours}h ${minutes}m remaining` : `${minutes}m remaining`;
}

function formatDuration(activatedAt: string | null, expiresAt: string): string | null {
  if (!activatedAt) return null;
  const days = Math.round((new Date(expiresAt).getTime() - new Date(activatedAt).getTime()) / 86_400_000);
  return `${days}-day grant`;
}

export function Access() {
  const { accessStatus, accessLoading, refreshAccessStatus } = useAuth();
  const navigate = useNavigate();
  const tilt = useTilt<HTMLDivElement>();
  const [code, setCode] = useState("");
  const [errorKind, setErrorKind] = useState<ErrorKind | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [justActivated, setJustActivated] = useState<{ activated_at: string; expires_at: string } | null>(null);

  useEffect(() => {
    refreshAccessStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrorKind(null);
    setErrorDetail(null);
    setBusy(true);
    try {
      const grant = await redeemAccessCode(code.trim());
      setJustActivated({ activated_at: grant.activated_at, expires_at: grant.expires_at });
      setCode("");
      await refreshAccessStatus();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setErrorKind(classifyError(message));
      setErrorDetail(message);
    } finally {
      setBusy(false);
    }
  }

  const hasAccess = accessStatus?.has_access ?? false;
  const activated = justActivated ?? (hasAccess && accessStatus?.activated_at && accessStatus?.expires_at
    ? { activated_at: accessStatus.activated_at, expires_at: accessStatus.expires_at }
    : null);

  return (
    <div>
      <div className="section-header">
        <h2>Access</h2>
        <span className="meta">Activate your football AI access</span>
      </div>

      <section className="status-result-shell" style={{ padding: "var(--space-5) 0" }}>
        <div
          className={`card card-pad status-result tilt-card ${
            accessLoading ? "" : hasAccess ? "active" : errorKind ? "suspended" : "pending"
          }`}
          style={{ maxWidth: 480, width: "100%" }}
          {...tilt}
        >
          <Mascot pose={hasAccess ? "celebrating" : errorKind ? "sad" : "thinking"} size={80} />

          {accessLoading && <p className="status-result-message">Checking your access...</p>}

          {!accessLoading && hasAccess && (
            <>
              <h1>Access activated</h1>
              <p className="status-result-message">
                Predictions, teams and matches are unlocked. {activated?.expires_at && formatRemaining(activated.expires_at)}
              </p>
              {activated && (
                <div className="status-result-explainer">
                  <h3>Grant details</h3>
                  <p>
                    Activated {new Date(activated.activated_at).toLocaleString()}
                    <br />
                    Expires {new Date(activated.expires_at).toLocaleString()}
                    {formatDuration(activated.activated_at, activated.expires_at) && (
                      <>
                        <br />
                        {formatDuration(activated.activated_at, activated.expires_at)}
                      </>
                    )}
                  </p>
                </div>
              )}
              <div className="status-result-actions">
                <button className="btn btn-lg" onClick={() => navigate("/app")}>
                  Enter the platform
                </button>
              </div>
            </>
          )}

          {!accessLoading && !hasAccess && (
            <>
              <h1>{accessStatus?.status === "expired" ? "Your access has expired" : "No active access yet"}</h1>
              <p className="status-result-message">
                Predictions, teams and matches stay locked until you redeem a code. A Super Admin issues one
                after confirming your payment outside the platform.
              </p>
            </>
          )}
        </div>
      </section>

      <div className="card card-pad" style={{ maxWidth: 420, margin: "0 auto" }}>
        <h3 style={{ marginTop: 0, fontSize: 15 }}>Enter access code</h3>
        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            Access code
            <input
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="XXXX-XXXX-XXXX"
              autoComplete="off"
              style={{ textTransform: "uppercase", fontFamily: "monospace", letterSpacing: 1 }}
            />
          </label>

          {errorKind && (
            <p className="auth-error">
              <strong>
                {ERROR_COPY[errorKind].icon} {ERROR_COPY[errorKind].title}
              </strong>
              {errorKind === "other" && errorDetail && <><br />{errorDetail}</>}
            </p>
          )}

          <button className="btn" type="submit" disabled={busy || !code.trim()} style={{ marginTop: 8 }}>
            {busy ? "Activating..." : "Activate access"}
          </button>
        </form>
      </div>
    </div>
  );
}
