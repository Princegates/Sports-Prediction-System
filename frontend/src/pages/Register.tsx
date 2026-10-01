import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { fetchBranding, registerAccount } from "../api";
import { Brand } from "../components/Brand";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { whatsappLink } from "../lib/whatsapp";
import type { Branding } from "../types";

const MINIMUM_AGE_YEARS = 18;

/** Today minus 18 years, as a YYYY-MM-DD string -- the date input's own
 * ``max`` so most browsers refuse to let the picker land on a too-recent
 * date at all. The server re-checks this regardless (see routes_auth.py's
 * register()), since nothing stops a hand-crafted request from skipping
 * the input entirely. */
function maxDateOfBirth(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - MINIMUM_AGE_YEARS);
  return d.toISOString().slice(0, 10);
}

export function Register() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  // Prefilled from a shared referral link (/register?ref=CODE) -- still a
  // plain editable field, since someone might instead type in a code a
  // friend read out to them rather than clicking a link at all.
  const [referralCode, setReferralCode] = useState(() => searchParams.get("ref") ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [branding, setBranding] = useState<Branding | null>(null);

  useEffect(() => {
    fetchBranding()
      .then(setBranding)
      .catch(() => {});
  }, []);

  const whatsapp = branding?.contact_whatsapp || null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await registerAccount({
        email,
        name,
        password,
        date_of_birth: dateOfBirth,
        referral_code: referralCode.trim() || undefined,
      });
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
                      --{" "}
                      <a href={whatsappLink(whatsapp, "Hi, I'd like an access code for Socca Intelligence.")} target="_blank" rel="noreferrer noopener">
                        contact admin on WhatsApp
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
        <Brand center className="auth-brand" />

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
          <label>
            Date of birth
            <input
              type="date"
              required
              max={maxDateOfBirth()}
              value={dateOfBirth}
              onChange={(e) => setDateOfBirth(e.target.value)}
              autoComplete="bday"
            />
          </label>
          <p className="setting-note" style={{ marginTop: -8 }}>
            You must be at least {MINIMUM_AGE_YEARS} to create an account.
          </p>

          {branding?.referral_enabled && (
            <>
              <label>
                Referral code (optional)
                <input
                  value={referralCode}
                  onChange={(e) => setReferralCode(e.target.value)}
                  placeholder="e.g. 7P3K9QX"
                  style={{ textTransform: "uppercase" }}
                />
              </label>
              <p className="setting-note" style={{ marginTop: -8 }}>
                Got one from a friend? Enter it and you'll both get {branding.referral_bonus_days} bonus day
                {branding.referral_bonus_days === 1 ? "" : "s"} of access.
              </p>
            </>
          )}

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
