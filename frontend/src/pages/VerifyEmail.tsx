import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { verifyEmail } from "../api";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { useAuth } from "../lib/AuthContext";

/** Landing page for the link in the signup confirmation email. Confirming
 * is what starts the free trial (see routes_auth.verify_email). */
export function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { user, refreshAccessStatus } = useAuth();
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!token) {
      setResult({ ok: false, message: "This link is missing its confirmation code. Open the link from the email again." });
      return;
    }
    verifyEmail(token)
      .then((r) => {
        setResult({ ok: true, message: r.message });
        if (user) void refreshAccessStatus();
      })
      .catch((err) => setResult({ ok: false, message: err instanceof Error ? err.message : String(err) }));
    // Runs once per token; refreshing access is a courtesy for a signed-in tab.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  return (
    <PublicShell title="Confirm Email — Socca Intelligence" description="Confirm your email address to start your free trial.">
      <div className="auth-shell">
        <div className="card card-pad auth-card" style={{ textAlign: "center" }}>
          <Mascot pose={result == null ? "thinking" : result.ok ? "celebrating" : "sad"} size={72} className="auth-mascot" />
          <h1 style={{ fontSize: 20, marginBottom: 8 }}>
            {result == null ? "Confirming your email..." : result.ok ? "You're all set" : "That link didn't work"}
          </h1>
          {result && <p style={{ fontSize: 14, color: "var(--text-secondary)", marginTop: 0 }}>{result.message}</p>}
          {result && (
            <Link className="btn" to={user ? "/app" : "/login"} style={{ marginTop: 12 }}>
              {user ? "Go to the app" : "Sign in"}
            </Link>
          )}
        </div>
      </div>
    </PublicShell>
  );
}
