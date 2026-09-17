import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { useAuth } from "../lib/AuthContext";

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/app";

  // A 403 means the credentials were right but the account isn't approved
  // yet, which is a different situation from a wrong password and deserves a
  // different next step.
  const awaitingApproval = error != null && /approv|pending|suspend/i.test(error);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PublicShell>
      <div className="auth-shell">
      <div className="card card-pad auth-card">
        <Mascot pose="idle" size={64} className="auth-mascot" />
        <div className="brand" style={{ marginBottom: 24, justifyContent: "center" }}>
          <span className="brand-mark">AI</span>
          <span className="brand-text">
            <strong>Match Intelligence</strong>
            <span>Football AI</span>
          </span>
        </div>

        <h1 style={{ fontSize: 20, marginBottom: 6 }}>Sign in</h1>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 20 }}>
          Access requires an approved account.
        </p>

        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            Email
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          <label>
            Password
            <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>

          {error && <p className="auth-error">{error}</p>}

          {awaitingApproval && (
            <p className="auth-hint">
              Your credentials are correct — the account just isn't live yet.{" "}
              <Link to="/account-status">Check your application status</Link>.
            </p>
          )}

          <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8 }}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 20, textAlign: "center" }}>
          No account yet? <Link to="/register">Request access</Link>
          <br />
          Waiting on approval? <Link to="/account-status">Check your status</Link>
        </p>
      </div>
      </div>
    </PublicShell>
  );
}
