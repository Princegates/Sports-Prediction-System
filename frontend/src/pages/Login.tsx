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

  // A 403 here only ever means suspended -- login itself has no other gate
  // any more, so this is a different situation from a wrong password.
  const isSuspended = error != null && /suspend/i.test(error);

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
          <span className="brand-mark">SI</span>
          <span className="brand-text">
            <strong>Socca Intelligence</strong>
            <span>Football prediction AI</span>
          </span>
        </div>

        <h1 style={{ fontSize: 20, marginBottom: 6 }}>Sign in</h1>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 20 }}>
          Predictions unlock once you redeem an access code -- signing in doesn't need one.
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

          {isSuspended && (
            <p className="auth-hint">
              Your credentials are correct — this account has been suspended. Contact your Super Admin.
            </p>
          )}

          <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8 }}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 20, textAlign: "center" }}>
          No account yet? <Link to="/register">Create one</Link>
          <br />
          Not sure if you have access? <Link to="/account-status">Check your status</Link>
        </p>
      </div>
      </div>
    </PublicShell>
  );
}
