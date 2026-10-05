import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { changePassword, fetchBranding, fetchMatchHistory } from "../api";
import { Mascot } from "../components/Mascot";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { useAuth } from "../lib/AuthContext";
import { whatsappLink } from "../lib/whatsapp";
import type { Branding, MatchHistoryEntry } from "../types";

/** A referral section only makes sense once there's a real bonus a shared
 * code would earn -- hidden entirely while a superadmin has turned the
 * program off (Settings -> Access -> Reward referrals), same as the
 * matching field on Register.tsx. */
function ReferAFriend({ code }: { code: string }) {
  const [branding, setBranding] = useState<Branding | null>(null);

  useEffect(() => {
    fetchBranding().then(setBranding).catch(() => {});
  }, []);

  if (!branding?.referral_enabled) return null;

  const link = `${window.location.origin}/register?ref=${code}`;
  const bonusDays = branding.referral_bonus_days;
  const shareMessage =
    `I'm using Socca Intelligence for AI football predictions -- sign up with my code ${code} and we both get ` +
    `${bonusDays} bonus day${bonusDays === 1 ? "" : "s"} of full access: ${link}`;

  return (
    <div className="card card-pad" style={{ marginBottom: 24 }}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>Refer a friend</h3>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0 }}>
        Share your code -- when someone signs up with it, you both get {bonusDays} bonus day{bonusDays === 1 ? "" : "s"}{" "}
        of access, stacked on top of whatever you already have.
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
        <span className="status-tag active" style={{ fontSize: 16, letterSpacing: "0.08em", padding: "6px 14px" }}>
          {code}
        </span>
        <CopyButton text={code} label="Copy code" />
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <CopyButton text={link} label="Copy link" className="btn ghost" />
        <a
          className="btn"
          href={whatsappLink("", shareMessage)}
          target="_blank"
          rel="noreferrer noopener"
        >
          Share on WhatsApp
        </a>
      </div>
    </div>
  );
}

function EditNameForm() {
  const { user, updateProfile } = useAuth();
  const [name, setName] = useState(user?.name ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    setBusy(true);
    try {
      await updateProfile(name.trim());
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="auth-form">
      <label>
        Display name
        <input required value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} />
      </label>
      {error && <p className="auth-error">{error}</p>}
      {saved && !error && <p style={{ fontSize: 12.5, color: "var(--good)", margin: 0 }}>Saved.</p>}
      <button className="btn" type="submit" disabled={busy || !name.trim()} style={{ marginTop: 8, alignSelf: "flex-start" }}>
        {busy ? "Saving..." : "Save name"}
      </button>
    </form>
  );
}

function EmailPreferences() {
  const { user, updatePreferences } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user) return null;

  async function toggle() {
    setError(null);
    setBusy(true);
    try {
      await updatePreferences({ notify_weekly_picks: !user!.notify_weekly_picks });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: busy ? "default" : "pointer" }}>
        <input type="checkbox" checked={user.notify_weekly_picks} disabled={busy} onChange={toggle} />
        <span style={{ fontSize: 13.5 }}>Email me when this week's picks are up</span>
      </label>
      <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: "6px 0 0" }}>
        One email a week at most, pointing back here -- never the picks themselves, never anything else.
      </p>
      {error && <p className="auth-error">{error}</p>}
    </div>
  );
}

function ChangePasswordForm() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setDone(false);
    setBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="auth-form">
      <label>
        Current password
        <input
          type="password"
          required
          autoComplete="current-password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
        />
      </label>
      <label>
        New password
        <input
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
        />
      </label>
      {error && <p className="auth-error">{error}</p>}
      {done && !error && <p style={{ fontSize: 12.5, color: "var(--good)", margin: 0 }}>Password changed.</p>}
      <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8, alignSelf: "flex-start" }}>
        {busy ? "Changing..." : "Change password"}
      </button>
    </form>
  );
}

export function Profile() {
  const { user } = useAuth();
  const [history, setHistory] = useState<MatchHistoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchMatchHistory()
      .then(setHistory)
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
  }, []);

  if (!user) return null;

  return (
    <div>
      <div className="section-header">
        <h2>Your profile</h2>
        <span className="meta">Personal account details and analysis history</span>
      </div>

      <div className="card card-pad" style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap", marginBottom: 24 }}>
        <Mascot pose="idle" size={72} />
        <div style={{ flex: 1, minWidth: 200 }}>
          <h1 style={{ fontSize: 20, margin: 0 }}>{user.name}</h1>
          <p style={{ margin: "4px 0 0", fontSize: 13.5, color: "var(--text-secondary)" }}>{user.email}</p>
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <span className="status-tag active">{user.role}</span>
            <span className="status-tag active">{user.status}</span>
          </div>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-muted)", textAlign: "right" }}>
          Member since
          <div style={{ fontSize: 14, color: "var(--text-primary)", fontWeight: 600 }}>
            {new Date(user.created_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}
          </div>
        </div>
      </div>

      <div className="section-header">
        <h2>Account settings</h2>
        <span className="meta">Update your display name or password</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16, marginBottom: 24 }}>
        <div className="card card-pad">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Display name</h3>
          <EditNameForm />
        </div>
        <div className="card card-pad">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Change password</h3>
          <ChangePasswordForm />
        </div>
        <div className="card card-pad">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Email preferences</h3>
          <EmailPreferences />
        </div>
      </div>

      {user.referral_code && <ReferAFriend code={user.referral_code} />}

      <div className="section-header">
        <h2>Recently analyzed</h2>
        <span className="meta">Matches you've opened -- visible only to you</span>
      </div>

      {error && <ErrorState message={error} />}
      {!error && history === null && <p className="badge-neutral">Loading your history…</p>}
      {!error && history !== null && history.length === 0 && (
        <EmptyState title="No matches viewed yet." hint="Open any match's AI Prediction tab and it will show up here." />
      )}
      {!error && history !== null && history.length > 0 && (
        <div className="card">
          {history.map(({ match, viewed_at }) => (
            <Link
              key={match.id}
              to={`/app/match/${match.id}`}
              className="transparency-row"
              style={{ display: "flex", padding: "12px 16px", borderBottom: "1px solid var(--border)", textDecoration: "none", color: "inherit" }}
            >
              <span className="name" style={{ width: "auto", flex: 1 }}>
                {match.home_team.name} vs {match.away_team.name}
                <span style={{ display: "block", fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>{match.league}</span>
              </span>
              <span className="pct" style={{ width: "auto", color: "var(--text-muted)", fontSize: 12 }}>
                Viewed {new Date(viewed_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
