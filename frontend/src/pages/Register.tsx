import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchBranding, registerAccount } from "../api";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { formatWhatsapp, whatsappLink } from "../lib/whatsapp";

export function Register() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [whatsapp, setWhatsapp] = useState<string | null>(null);

  useEffect(() => {
    fetchBranding()
      .then((b) => setWhatsapp(b.contact_whatsapp || null))
      .catch(() => {});
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await registerAccount({ email, name, password });
      setDone(result.message);
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <PublicShell
        title="Create an Account — Socca Intelligence"
        description="Create a Socca Intelligence account for calibrated football predictions across every major competition, with a free trial to start."
      >
        <div className="auth-shell">
          <div className="card card-pad auth-card">
            <Mascot pose="celebrating" size={72} className="auth-mascot" />
            <h1 style={{ fontSize: 20, marginBottom: 10, textAlign: "center" }}>Account created</h1>
            <p style={{ fontSize: 13.5, color: "var(--text-secondary)" }}>{done}</p>

            <div className="register-next-steps">
              <h3>What happens now</h3>
              <ol>
                <li>Sign in right away -- there's no approval queue to wait on.</li>
                <li>
                  When your trial ends, arrange payment with a Super Admin outside the platform
                  {whatsapp ? (
                    <>
                      {" "}
                      -- message{" "}
                      <a href={whatsappLink(whatsapp, "Hi, I'd like an access code for Socca Intelligence.")} target="_blank" rel="noreferrer noopener">
                        {formatWhatsapp(whatsapp)} on WhatsApp
                      </a>{" "}
                      (WhatsApp only). Once confirmed, they'll hand you an access code.
                    </>
                  ) : (
                    ". Once confirmed, they'll hand you an access code."
                  )}
                </li>
                <li>Redeem the code from the Access page and the full platform unlocks.</li>
              </ol>
            </div>

            <button className="btn" style={{ marginTop: 16 }} onClick={() => navigate("/login")}>
              Go to sign in
            </button>
          </div>
        </div>
      </PublicShell>
    );
  }

  return (
    <PublicShell
      title="Create an Account — Socca Intelligence"
      description="Create a Socca Intelligence account for calibrated football predictions across every major competition, with a free trial to start."
    >
      <div className="auth-shell">
      <div className="card card-pad auth-card">
        <div className="brand" style={{ marginBottom: 24 }}>
          <span className="brand-mark">SI</span>
          <span className="brand-text">
            <strong>Socca Intelligence</strong>
            <span>Football prediction AI</span>
          </span>
        </div>

        <h1 style={{ fontSize: 20, marginBottom: 6 }}>Create an account</h1>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 20 }}>
          Sign in right after creating your account. Predictions unlock once you redeem an access code, which
          a Super Admin issues after confirming payment outside the platform.
        </p>

        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            Name
            <input required value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
          </label>
          <label>
            Email
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          <label>
            Password
            <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </label>

          {error && <p className="auth-error">{error}</p>}

          <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8 }}>
            {busy ? "Creating account..." : "Create account"}
          </button>
        </form>

        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 20, textAlign: "center" }}>
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
      </div>
    </PublicShell>
  );
}
