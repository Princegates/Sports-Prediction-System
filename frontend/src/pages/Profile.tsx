import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  changePassword, fetchBranding, fetchMatchHistory, fetchReferralStats, pauseAccount, redeemAccessCode, revokeOtherSessions,
} from "../api";
import { Mascot } from "../components/Mascot";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { useAuth } from "../lib/AuthContext";
import { whatsappLink } from "../lib/whatsapp";
import type { Branding, MatchHistoryEntry, ReferralStats } from "../types";

/** Where a member enters the access code a Super Admin sent them (the full
 * page, with the price and WhatsApp contact, is /app/access). Shown with their
 * current status so "do I have access, and until when?" is answered here too. */
function AccessCodeCard() {
  const { accessStatus, refreshAccessStatus } = useAuth();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    refreshAccessStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleRedeem(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await redeemAccessCode(code.trim());
      setCode("");
      setDone(true);
      await refreshAccessStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const status = accessStatus?.status;
  const until = accessStatus?.expires_at ? new Date(accessStatus.expires_at).toLocaleDateString() : null;
  const summary =
    status === "unlimited" ? "Unlimited -- Super Admin accounts never expire."
    : status === "active" ? `Active${until ? ` until ${until}` : ""}.`
    : status === "expired" ? `Expired${until ? ` on ${until}` : ""}. Enter a new code to continue.`
    : status === "none" ? "No access yet. Enter the access code you were sent to unlock predictions."
    : "Checking your access...";

  return (
    <div className="card card-pad" style={{ marginBottom: 24 }}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>Access code</h3>
      <p style={{ margin: "0 0 12px", fontSize: 13.5, color: "var(--text-secondary)" }}>
        {status && <span className={`status-tag ${status === "none" ? "pending" : status === "expired" ? "suspended" : "active"}`}>{status}</span>}{" "}
        {summary}
      </p>
      {status !== "unlimited" && (
        <form onSubmit={handleRedeem} className="auth-form" style={{ maxWidth: 420 }}>
          <label>
            {status === "active" ? "Add time with another code" : "Your access code"}
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="XXXX-XXXX-XXXX"
              autoComplete="off"
              spellCheck={false}
              style={{ fontFamily: "monospace", textTransform: "uppercase" }}
            />
          </label>
          {error && <p className="auth-error">{error}</p>}
          {done && <p className="meta" style={{ color: "var(--success, inherit)" }}>Code redeemed -- your access has been updated.</p>}
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn" type="submit" disabled={busy || !code.trim()}>
              {busy ? "Redeeming..." : "Redeem code"}
            </button>
            <Link to="/app/access" className="meta">Need a code? See how to get one</Link>
          </div>
        </form>
      )}
    </div>
  );
}

/** A referral section only makes sense once there's a real bonus a shared
 * code would earn -- hidden entirely while a superadmin has turned the
 * program off (Settings -> Access -> Reward referrals), same as the
 * matching field on Register.tsx. */
function ReferAFriend({ code }: { code: string }) {
  const [branding, setBranding] = useState<Branding | null>(null);
  const [stats, setStats] = useState<ReferralStats | null>(null);

  useEffect(() => {
    fetchBranding().then(setBranding).catch(() => {});
    fetchReferralStats().then(setStats).catch(() => {});
  }, []);

  if (!branding?.referral_enabled) return null;

  const link = `${window.location.origin}/register?ref=${code}`;
  const bonusDays = branding.referral_bonus_days;
  const shareMessage =
    `I'm using Socca Intelligence for AI football predictions -- sign up with my code ${code} and get ` +
    `${bonusDays} bonus day${bonusDays === 1 ? "" : "s"} of full access on top of the free trial: ${link}`;

  return (
    <div className="card card-pad" style={{ marginBottom: 24 }}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>Refer a friend</h3>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0 }}>
        Share your code. Your friend gets {bonusDays} bonus day{bonusDays === 1 ? "" : "s"} when they sign up, and you get{" "}
        {bonusDays} day{bonusDays === 1 ? "" : "s"} added to your access when they buy their first access code.
      </p>
      {stats && stats.joined > 0 && (
        <p style={{ fontSize: 13, marginTop: 0 }}>
          <strong>{stats.joined}</strong> joined with your code · <strong>{stats.paid}</strong> subscribed · you've earned{" "}
          <strong>
            {stats.paid * stats.bonus_days_each} day{stats.paid * stats.bonus_days_each === 1 ? "" : "s"}
          </strong>
        </p>
      )}
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
  const { adoptSession } = useAuth();
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
      adoptSession(await changePassword(currentPassword, newPassword));
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
      {done && !error && <p style={{ fontSize: 12.5, color: "var(--good)", margin: 0 }}>Password changed. Other devices have been signed out.</p>}
      <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8, alignSelf: "flex-start" }}>
        {busy ? "Changing..." : "Change password"}
      </button>
    </form>
  );
}

function SignOutOtherDevices() {
  const { adoptSession } = useAuth();
  const [state, setState] = useState<"idle" | "busy" | "done" | string>("idle");

  async function handleClick() {
    setState("busy");
    try {
      adoptSession(await revokeOtherSessions());
      setState("done");
    } catch (err) {
      setState(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0 }}>
        Signed in on a phone or computer you no longer use? This signs out everywhere except here.
      </p>
      <button type="button" className="btn ghost" onClick={handleClick} disabled={state === "busy"}>
        {state === "busy" ? "Signing out..." : "Sign out other devices"}
      </button>
      {state === "done" && <p style={{ fontSize: 13, color: "var(--good)", marginBottom: 0 }}>Done -- only this device is signed in now.</p>}
      {state !== "idle" && state !== "busy" && state !== "done" && <p className="auth-error">{state}</p>}
    </>
  );
}

const BREAK_CHOICES = [
  { days: 1, label: "24 hours" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
];

/** Self-set cool-off. Two clicks on purpose -- choosing a length, then
 * confirming -- since it can't be undone early (see User.paused_until). */
function TakeABreak() {
  const { user, setCurrentUser } = useAuth();
  const [choice, setChoice] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pausedUntil = user?.paused_until ? new Date(user.paused_until) : null;
  const paused = pausedUntil != null && pausedUntil.getTime() > Date.now();

  async function confirm() {
    if (choice == null) return;
    setBusy(true);
    setError(null);
    try {
      setCurrentUser(await pauseAccount(choice));
      setChoice(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0 }}>
        Hide every prediction and pick from your account for a while. You can't end a break early, so pick a length you mean.
      </p>
      {paused && (
        <p style={{ fontSize: 13, marginTop: 0 }}>
          On a break until <strong>{pausedUntil.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</strong>.
          You can make it longer, not shorter.
        </p>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {BREAK_CHOICES.map((c) => (
          <button
            key={c.days}
            type="button"
            className={`btn ghost btn-sm${choice === c.days ? " active" : ""}`}
            onClick={() => setChoice(c.days)}
          >
            {c.label}
          </button>
        ))}
      </div>
      {choice != null && (
        <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 13 }}>Pause for {BREAK_CHOICES.find((c) => c.days === choice)?.label}?</span>
          <button type="button" className="btn btn-sm" onClick={confirm} disabled={busy}>
            {busy ? "Starting..." : "Start break"}
          </button>
          <button type="button" className="btn ghost btn-sm" onClick={() => setChoice(null)} disabled={busy}>
            Cancel
          </button>
        </div>
      )}
      {error && <p className="auth-error">{error}</p>}
    </>
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

      <AccessCodeCard />

      <div className="section-header">
        <h2>Account settings</h2>
        <span className="meta">Name, password, devices and breaks</span>
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
        <div className="card card-pad">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Devices</h3>
          <SignOutOtherDevices />
        </div>
        <div className="card card-pad">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Take a break</h3>
          <TakeABreak />
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
