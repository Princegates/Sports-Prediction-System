import { useState } from "react";
import { Link } from "react-router-dom";
import { forgotPassword } from "../api";
import { Brand } from "../components/Brand";
import { PublicShell } from "../components/PublicShell";

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      setSent((await forgotPassword(email)).message);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PublicShell title="Reset Password — Socca Intelligence" description="Get a link to choose a new password for your Socca Intelligence account.">
      <div className="auth-shell">
        <div className="card card-pad auth-card">
          <Brand center className="auth-brand" />
          <h1 style={{ fontSize: 20, marginBottom: 6 }}>Forgot your password?</h1>
          {sent ? (
            <>
              <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>{sent}</p>
              <p style={{ fontSize: 13, color: "var(--text-muted)" }}>Not there? Check your spam folder.</p>
              <Link className="btn" to="/login" style={{ marginTop: 8 }}>
                Back to sign in
              </Link>
            </>
          ) : (
            <form onSubmit={handleSubmit} className="auth-form">
              <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0 }}>
                Enter the email you signed up with and we'll send you a link to choose a new one.
              </p>
              <label>
                Email
                <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
              </label>
              {error && <p className="auth-error">{error}</p>}
              <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8 }}>
                {busy ? "Sending..." : "Send reset link"}
              </button>
              <p style={{ fontSize: 13, textAlign: "center", marginBottom: 0 }}>
                <Link to="/login">Back to sign in</Link>
              </p>
            </form>
          )}
        </div>
      </div>
    </PublicShell>
  );
}
