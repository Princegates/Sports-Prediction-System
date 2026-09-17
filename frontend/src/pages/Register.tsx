import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { registerAccount } from "../api";
import { Mascot } from "../components/Mascot";

export function Register() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [paymentReference, setPaymentReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await registerAccount({ email, name, password, payment_reference: paymentReference || undefined });
      setDone(result.message);
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="auth-shell">
        <div className="card card-pad auth-card">
          <Mascot pose="celebrating" size={72} className="auth-mascot" />
          <h1 style={{ fontSize: 20, marginBottom: 10, textAlign: "center" }}>Account created</h1>
          <p style={{ fontSize: 13.5, color: "var(--text-secondary)" }}>{done}</p>
          <button className="btn" style={{ marginTop: 16 }} onClick={() => navigate("/login")}>
            Go to sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-shell">
      <div className="card card-pad auth-card">
        <div className="brand" style={{ marginBottom: 24 }}>
          <span className="brand-mark">AI</span>
          <span className="brand-text">
            <strong>Match Intelligence</strong>
            <span>Football AI</span>
          </span>
        </div>

        <h1 style={{ fontSize: 20, marginBottom: 6 }}>Create an account</h1>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 20 }}>
          New accounts are reviewed and approved by an admin after payment confirmation before you can sign in.
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
            Payment reference <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>(optional -- e.g. mobile money transaction ID)</span>
            <input value={paymentReference} onChange={(e) => setPaymentReference(e.target.value)} placeholder="MOMO-XXXXXXX" />
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
  );
}
