import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchAccountStatus } from "../api";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import type { AccountStatus as Status } from "../types";

/**
 * "Where is my application?" page.
 *
 * Without this, a user who registers and waits has no way to distinguish
 * "still queued" from "rejected" from "I mistyped my email" -- their only
 * signal is a login that keeps failing. Requires the password, so it tells a
 * stranger nothing, and the backend returns an identical pending response for
 * unknown emails so it can't be used to discover which addresses registered.
 */

const STATUS_COPY: Record<Status["status"], { title: string; pose: "idle" | "thinking" | "celebrating" | "sad"; tone: string }> = {
  pending: { title: "Awaiting Super Admin approval", pose: "thinking", tone: "pending" },
  active: { title: "Approved — you're in", pose: "celebrating", tone: "active" },
  suspended: { title: "This account is suspended", pose: "sad", tone: "suspended" },
};

export function AccountStatus() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      setStatus(await fetchAccountStatus(email, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (status) {
    const copy = STATUS_COPY[status.status];
    return (
      <PublicShell>
        <section className="status-result-shell">
          <div className={`card card-pad status-result ${copy.tone}`}>
            <Mascot pose={copy.pose} size={80} />
            <h1>{copy.title}</h1>
            <p className="status-result-message">{status.message}</p>

            {status.submitted_at && (
              <p className="status-result-meta">
                Registered {new Date(status.submitted_at).toLocaleString()}
              </p>
            )}

            {status.status === "pending" && (
              <div className="status-result-explainer">
                <h3>What happens next</h3>
                <p>
                  A Super Admin reviews each application by hand, confirming any payment reference you
                  submitted out of band. There's no automatic approval and no trial tier — until someone
                  signs off, the whole API rejects the account. Nothing is required from you in the
                  meantime.
                </p>
              </div>
            )}

            {status.status === "active" && (
              <Link className="btn btn-lg" to="/login">
                Sign in
              </Link>
            )}

            <div className="status-result-actions">
              <button className="btn ghost" onClick={() => setStatus(null)}>
                Check another account
              </button>
              <Link className="btn ghost" to="/">
                Back to home
              </Link>
            </div>
          </div>
        </section>
      </PublicShell>
    );
  }

  return (
    <PublicShell>
      <section className="status-result-shell">
        <div className="card card-pad auth-card">
          <Mascot pose="idle" size={64} className="auth-mascot" />
          <h1 style={{ fontSize: 22, marginBottom: 6, textAlign: "center" }}>Check your application</h1>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 20, textAlign: "center" }}>
            Registered but can't sign in yet? Enter the same credentials to see where your account
            stands.
          </p>

          <form onSubmit={handleSubmit} className="auth-form">
            <label>
              Email
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>

            {error && <p className="auth-error">{error}</p>}

            <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8 }}>
              {busy ? "Checking..." : "Check status"}
            </button>
          </form>

          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 20, textAlign: "center" }}>
            Haven't registered yet? <Link to="/register">Request access</Link>
          </p>
        </div>
      </section>
    </PublicShell>
  );
}
