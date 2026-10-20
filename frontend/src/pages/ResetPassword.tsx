import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { resetPassword } from "../api";
import { Brand } from "../components/Brand";
import { PublicShell } from "../components/PublicShell";

export function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      setDone((await resetPassword(token, password)).message);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PublicShell title="Choose a New Password — Socca Intelligence" description="Choose a new password for your Socca Intelligence account.">
      <div className="auth-shell">
        <div className="card card-pad auth-card">
          <Brand center className="auth-brand" />
          <h1 style={{ fontSize: 20, marginBottom: 6 }}>Choose a new password</h1>
          {done ? (
            <>
              <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>{done}</p>
              <Link className="btn" to="/login" style={{ marginTop: 8 }}>
                Sign in
              </Link>
            </>
          ) : !token ? (
            <p className="auth-error">
              This link is missing its reset code. <Link to="/forgot-password">Ask for a new link</Link>.
            </p>
          ) : (
            <form onSubmit={handleSubmit} className="auth-form">
              <label>
                New password
                <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
              </label>
              <label>
                Repeat it
                <input type="password" required minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
              </label>
              {error && (
                <p className="auth-error">
                  {error} {/expired|used|invalid/i.test(error) && <Link to="/forgot-password">Ask for a new link</Link>}
                </p>
              )}
              <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8 }}>
                {busy ? "Saving..." : "Save new password"}
              </button>
              <p style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 0 }}>
                Saving signs you out on every device, so anyone else using your account loses access.
              </p>
            </form>
          )}
        </div>
      </div>
    </PublicShell>
  );
}
