import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  activateUser,
  createAccessCode,
  deleteUser,
  demoteUser,
  extendUserAccess,
  fetchAccessCodes,
  fetchAdminOverview,
  fetchAdminUsers,
  fetchSettings,
  promoteUser,
  reinstateUser,
  resendUserVerification,
  resendAccessCode,
  revealAccessCode,
  revokeAccessCode,
  revokeUserAccess,
  sendAdminMessage,
  setUserAIGenerationLimit,
  suspendUser,
} from "../api";
import { useAuth } from "../lib/AuthContext";
import type { AccessCode, AdminMessageResponse, AdminOverview, AdminUser, UserStatus } from "../types";
import { ErrorState } from "../components/ErrorState";

const FILTERS: { label: string; value: UserStatus | "all" }[] = [
  { label: "Active", value: "active" },
  { label: "Suspended", value: "suspended" },
  { label: "All", value: "all" },
];

const ACCESS_TONE: Record<AdminUser["access_status"], string> = {
  unlimited: "active",
  active: "active",
  expired: "pending",
  none: "suspended",
};

const DURATION_PRESETS = [1, 3, 7, 14, 30, 60, 90, 180, 365];

const CODE_FILTERS: { label: string; value: AccessCode["status"] | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Active", value: "active" },
  { label: "Exhausted", value: "exhausted" },
  { label: "Expired", value: "expired" },
  { label: "Revoked", value: "revoked" },
];

const CODE_TONE: Record<AccessCode["status"], string> = {
  active: "active",
  exhausted: "pending",
  expired: "pending",
  revoked: "suspended",
};

export function AdminUsers() {
  const { user: currentUser } = useAuth();
  const [filter, setFilter] = useState<UserStatus | "all">("active");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const [onlyPending, setOnlyPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [msgTo, setMsgTo] = useState("");
  const [msgSubject, setMsgSubject] = useState("");
  const [msgBody, setMsgBody] = useState("");
  const [msgBusy, setMsgBusy] = useState(false);
  const [msgError, setMsgError] = useState<string | null>(null);
  const [msgResult, setMsgResult] = useState<AdminMessageResponse | null>(null);
  const msgCardRef = useRef<HTMLDivElement>(null);

  const [codes, setCodes] = useState<AccessCode[] | null>(null);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [codeBusy, setCodeBusy] = useState(false);
  const [revealed, setRevealed] = useState<Record<number, string>>({});
  const [justCreated, setJustCreated] = useState<AccessCode | null>(null);
  const [justCreatedWasResend, setJustCreatedWasResend] = useState(false);
  const [durationPreset, setDurationPreset] = useState("30");
  const [durationDays, setDurationDays] = useState("30");
  const [sendByEmail, setSendByEmail] = useState(true);
  const [assignedEmail, setAssignedEmail] = useState("");
  const [codeNotes, setCodeNotes] = useState("");
  const [codeFilter, setCodeFilter] = useState<AccessCode["status"] | "all">("all");
  const [codeSearch, setCodeSearch] = useState("");

  const codeStats = {
    active: codes?.filter((c) => c.status === "active").length ?? 0,
    exhausted: codes?.filter((c) => c.status === "exhausted").length ?? 0,
    expired: codes?.filter((c) => c.status === "expired").length ?? 0,
    revoked: codes?.filter((c) => c.status === "revoked").length ?? 0,
  };

  const visibleCodes = (codes ?? []).filter((c) => {
    if (codeFilter !== "all" && c.status !== codeFilter) return false;
    const q = codeSearch.trim().toLowerCase();
    if (!q) return true;
    return (
      c.code.toLowerCase().includes(q) ||
      (c.notes ?? "").toLowerCase().includes(q) ||
      (c.assigned_email ?? "").toLowerCase().includes(q)
    );
  });

  function handleDurationPresetChange(value: string) {
    setDurationPreset(value);
    if (value !== "custom") setDurationDays(value);
  }

  function load() {
    setError(null);
    fetchAdminUsers(filter === "all" ? undefined : filter)
      .then(setUsers)
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
    // The overview is refreshed alongside the list so its counts can't sit
    // stale after an action.
    fetchAdminOverview().then(setOverview).catch(() => setOverview(null));
  }

  function loadCodes() {
    fetchAccessCodes()
      .then(setCodes)
      .catch((err) => setCodeError(String(err instanceof Error ? err.message : err)));
  }

  useEffect(() => {
    setUsers(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  useEffect(() => {
    loadCodes();
    // Pre-fill duration from the platform-wide default (Settings -> Access)
    // -- just an initial value, not a live sync, so it never fights with
    // whatever the admin is mid-typing.
    fetchSettings()
      .then((s) => {
        const days = Number(s.values.default_code_duration_days);
        if (!Number.isFinite(days) || days <= 0) return;
        const preset = String(days);
        setDurationDays(preset);
        setDurationPreset(DURATION_PRESETS.includes(days) ? preset : "custom");
      })
      .catch(() => {});
  }, []);

  async function withBusy(id: number, action: () => Promise<unknown>) {
    setBusyId(id);
    try {
      await action();
      load();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleCreateCode(e: React.FormEvent) {
    e.preventDefault();
    setCodeError(null);
    setCodeBusy(true);
    try {
      const created = await createAccessCode({
        duration_days: Number(durationDays),
        assigned_user_email: assignedEmail.trim(),
        notes: codeNotes.trim() || undefined,
        send_email: sendByEmail,
      });
      setJustCreated(created);
      setJustCreatedWasResend(false);
      setAssignedEmail("");
      setCodeNotes("");
      loadCodes();
    } catch (err) {
      setCodeError(String(err instanceof Error ? err.message : err));
    } finally {
      setCodeBusy(false);
    }
  }

  async function handleRevokeCode(id: number) {
    setCodeBusy(true);
    try {
      await revokeAccessCode(id);
      loadCodes();
    } catch (err) {
      setCodeError(String(err instanceof Error ? err.message : err));
    } finally {
      setCodeBusy(false);
    }
  }

  function toggleReveal(id: number) {
    if (id in revealed) {
      setRevealed((r) => {
        const { [id]: _drop, ...rest } = r;
        return rest;
      });
      return;
    }
    setCodeError(null);
    revealAccessCode(id)
      .then((full) => setRevealed((r) => ({ ...r, [id]: full.code })))
      .catch((err) => setCodeError(String(err instanceof Error ? err.message : err)));
  }

  async function handleResendCode(id: number) {
    setCodeError(null);
    setCodeBusy(true);
    try {
      const resent = await resendAccessCode(id);
      setJustCreated(resent);
      setJustCreatedWasResend(true);
    } catch (err) {
      setCodeError(String(err instanceof Error ? err.message : err));
    } finally {
      setCodeBusy(false);
    }
  }

  async function handleExtend(id: number) {
    const raw = window.prompt("Extend access by how many days?", "30");
    if (!raw) return;
    const days = Number(raw);
    if (!Number.isFinite(days) || days <= 0) return;
    await withBusy(id, () => extendUserAccess(id, days));
  }

  async function handleDelete(u: AdminUser) {
    // Typing the email back is the confirmation: this can't be undone.
    const typed = window.prompt(
      `Delete ${u.email} permanently?\n\nThis removes the account, its access, chat history and saved slips. ` +
        "It cannot be undone. Type the email address to confirm.",
    );
    if (typed == null) return;
    if (typed.trim().toLowerCase() !== u.email.toLowerCase()) {
      setError("The email didn't match -- nothing was deleted.");
      return;
    }
    await withBusy(u.id, () => deleteUser(u.id));
  }

  const pendingUsers = (users ?? []).filter((u) => u.activation_pending);
  const shownUsers = onlyPending ? pendingUsers : users;

  async function handleResendLink(u: AdminUser) {
    setNotice(null);
    await withBusy(u.id, async () => {
      await resendUserVerification(u.id);
      setNotice(`Confirmation link sent to ${u.email}.`);
    });
  }

  async function handleActivate(u: AdminUser) {
    if (
      !window.confirm(
        `Activate ${u.email} without them confirming their email?\n\nThis starts their free trial right away. ` +
          "Only do this if you trust the address is theirs, or they can't receive the link.",
      )
    )
      return;
    setNotice(null);
    await withBusy(u.id, async () => {
      await activateUser(u.id);
      setNotice(`${u.email} is activated and their free trial has started.`);
    });
  }

  // Fills the message form for everyone not yet activated -- at most the 25
  // a single send allows -- with a starting draft if nothing's been written.
  function messageEveryonePending() {
    setMsgTo(pendingUsers.slice(0, 25).map((u) => u.email).join(", "));
    setMsgResult(null);
    setMsgError(null);
    if (!msgSubject.trim()) setMsgSubject("Activate your account to start your free trial");
    if (!msgBody.trim()) {
      setMsgBody(
        "Hi,\n\nYou signed up but haven't confirmed your email yet, so your free trial hasn't started. " +
          "Open the confirmation link we emailed you (check your spam or junk folder too) and it starts straight away.\n\n" +
          "Can't find it? Reply to this message and we'll sort it out for you.",
      );
    }
    msgCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function startMessage(email: string) {
    setMsgTo(email);
    setMsgResult(null);
    setMsgError(null);
    msgCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function handleSendMessage(e: React.FormEvent) {
    e.preventDefault();
    setMsgError(null);
    setMsgResult(null);
    const recipients = msgTo
      .split(/[\s,;]+/)
      .map((r) => r.trim())
      .filter(Boolean);
    if (recipients.length === 0) {
      setMsgError("Add at least one email address.");
      return;
    }
    setMsgBusy(true);
    try {
      const result = await sendAdminMessage({ recipients, subject: msgSubject.trim(), message: msgBody.trim() });
      setMsgResult(result);
      // Keep the form for anything that didn't go out (so it can be fixed and
      // resent); clear it once every recipient got the message.
      if (result.results.every((r) => r.sent) && result.not_registered.length === 0) {
        setMsgTo("");
        setMsgSubject("");
        setMsgBody("");
      }
    } catch (err) {
      setMsgError(String(err instanceof Error ? err.message : err));
    } finally {
      setMsgBusy(false);
    }
  }

  async function handleRevokeAccess(id: number) {
    await withBusy(id, () => revokeUserAccess(id));
  }

  async function handleSetAIGenerationLimit(u: AdminUser) {
    const raw = window.prompt(
      "AI Generation runs per day for this account (including Refresh). Leave blank to use the " +
        "site-wide default (Settings -> Booking codes).",
      u.ai_generation_daily_limit_override === null ? "" : String(u.ai_generation_daily_limit_override),
    );
    if (raw === null) return;
    const trimmed = raw.trim();
    if (trimmed === "") {
      await withBusy(u.id, () => setUserAIGenerationLimit(u.id, null));
      return;
    }
    const limit = Number(trimmed);
    if (!Number.isFinite(limit) || limit < 0) return;
    await withBusy(u.id, () => setUserAIGenerationLimit(u.id, Math.floor(limit)));
  }

  if (error && users === null) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="admin-header">
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>User access</h1>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0" }}>
            Every account can sign in immediately -- generate an access code below once you've confirmed
            payment out of band, and hand it to the user to redeem.
          </p>
        </div>
        <div className="admin-filters">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              className={`btn ghost${filter === f.value ? " active" : ""}`}
              onClick={() => setFilter(f.value)}
              aria-pressed={filter === f.value}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {overview && (
        <div className={`admin-overview${overview.users_without_access > 0 ? " has-queue" : ""}`}>
          <div className="admin-overview-tile primary">
            <span className="admin-overview-value">{overview.users_without_access}</span>
            <span className="admin-overview-label">
              {overview.users_without_access === 1 ? "account has" : "accounts have"} no access grant
            </span>
          </div>
          {overview.unverified_users > 0 && (
            <div className="admin-overview-tile">
              <span className="admin-overview-value">{overview.unverified_users}</span>
              <span className="admin-overview-label">Not activated (email unconfirmed)</span>
            </div>
          )}
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{overview.active_access_grants}</span>
            <span className="admin-overview-label">Active access grants</span>
          </div>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{overview.active_users}</span>
            <span className="admin-overview-label">Active members</span>
          </div>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{overview.suspended_users}</span>
            <span className="admin-overview-label">Suspended</span>
          </div>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{overview.superadmins}</span>
            <span className="admin-overview-label">Super admins</span>
          </div>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{overview.predictions_generated.toLocaleString()}</span>
            <span className="admin-overview-label">Predictions stored</span>
          </div>
        </div>
      )}

      {error && <p className="auth-error" style={{ marginBottom: 12 }}>{error}</p>}
      {notice && <p className="setting-note" role="status" style={{ marginBottom: 12 }}>{notice}</p>}

      {pendingUsers.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 12 }}>
          <button
            className={`btn ghost${onlyPending ? " active" : ""}`}
            aria-pressed={onlyPending}
            onClick={() => setOnlyPending((v) => !v)}
          >
            Not activated ({pendingUsers.length})
          </button>
          <button className="btn ghost" onClick={messageEveryonePending}>
            Message everyone not activated
          </button>
        </div>
      )}

      <div className="card card-pad" ref={msgCardRef} style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 15 }}>Send a message</h3>
        <p className="setting-note" style={{ marginTop: 0 }}>
          Emails registered members directly from the site's own sender. Separate several addresses with commas or
          spaces. Anyone without an account here is skipped, not emailed.
        </p>
        <form onSubmit={handleSendMessage} className="auth-form">
          <label>
            To
            <input
              required
              value={msgTo}
              onChange={(e) => setMsgTo(e.target.value)}
              placeholder="member@example.com"
              autoComplete="off"
            />
          </label>
          <label>
            Subject
            <input required maxLength={150} value={msgSubject} onChange={(e) => setMsgSubject(e.target.value)} />
          </label>
          <label>
            Message
            <textarea
              required
              rows={6}
              maxLength={5000}
              value={msgBody}
              onChange={(e) => setMsgBody(e.target.value)}
              style={{ resize: "vertical", font: "inherit" }}
            />
          </label>
          {msgError && <p className="auth-error">{msgError}</p>}
          {msgResult && (
            <div role="status" className="setting-note">
              {msgResult.results.filter((r) => r.sent).length > 0 && (
                <p style={{ margin: "0 0 4px" }}>
                  Sent to {msgResult.results.filter((r) => r.sent).map((r) => r.email).join(", ")}.
                </p>
              )}
              {msgResult.results.filter((r) => !r.sent).map((r) => (
                <p key={r.email} className="auth-error" style={{ margin: "0 0 4px" }}>
                  Couldn't send to {r.email}: {r.error ?? "unknown error"}
                </p>
              ))}
              {msgResult.not_registered.length > 0 && (
                <p className="auth-error" style={{ margin: 0 }}>
                  No account for {msgResult.not_registered.join(", ")} -- not emailed.
                </p>
              )}
            </div>
          )}
          <button className="btn" type="submit" disabled={msgBusy || !msgTo.trim() || !msgSubject.trim() || !msgBody.trim()}>
            {msgBusy ? "Sending..." : "Send message"}
          </button>
        </form>
      </div>


      <div className="card admin-table-wrapper">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Status</th>
              <th>Role</th>
              <th>Access</th>
              <th>Joined</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users === null && (
              <tr>
                <td colSpan={7}>Loading...</td>
              </tr>
            )}
            {shownUsers !== null && shownUsers.length === 0 && <tr><td colSpan={7}>No users in this view.</td></tr>}
            {shownUsers?.map((u) => {
              const isSelf = u.id === currentUser?.id;
              const busy = busyId === u.id;
              return (
                <tr key={u.id}>
                  <td>{u.name}</td>
                  <td>{u.email}</td>
                  <td>
                    <span className={`status-tag ${u.status}`}>{u.status}</span>
                    {u.activation_pending && (
                      <div style={{ marginTop: 4 }}>
                        <span className="status-tag pending" title="Hasn't confirmed their email, so their free trial hasn't started">
                          not activated
                        </span>
                      </div>
                    )}
                  </td>
                  <td>{u.role}</td>
                  <td>
                    <span className={`status-tag ${ACCESS_TONE[u.access_status]}`}>{u.access_status}</span>
                    {u.access_expires_at && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3 }}>
                        {u.access_status === "expired" ? "expired " : "until "}
                        {new Date(u.access_expires_at).toLocaleDateString()}
                      </div>
                    )}
                    {u.ai_generation_daily_limit_override !== null && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3 }}>
                        {u.ai_generation_daily_limit_override}/day AI Generation
                      </div>
                    )}
                  </td>
                  <td>{new Date(u.created_at).toLocaleDateString()}</td>
                  <td>
                    <div className="admin-actions">
                      {u.access_status !== "none" && u.access_status !== "unlimited" && (
                        <button className="btn ghost" disabled={busy} onClick={() => handleExtend(u.id)}>
                          Extend
                        </button>
                      )}
                      {u.access_status !== "none" && u.access_status !== "unlimited" && (
                        <button className="btn ghost" disabled={busy} onClick={() => handleRevokeAccess(u.id)}>
                          Revoke access
                        </button>
                      )}
                      {u.role !== "superadmin" && (
                        <button className="btn ghost" disabled={busy} onClick={() => handleSetAIGenerationLimit(u)}>
                          AI limit
                        </button>
                      )}
                      {u.activation_pending && (
                        <>
                          <button className="btn ghost" disabled={busy} onClick={() => handleResendLink(u)}>
                            Resend link
                          </button>
                          <button className="btn" disabled={busy} onClick={() => handleActivate(u)}>
                            Activate
                          </button>
                        </>
                      )}
                      <button className="btn ghost" disabled={busy} onClick={() => startMessage(u.email)}>
                        Message
                      </button>
                      {u.status === "suspended" && (
                        <button
                          className="btn"
                          disabled={busy}
                          onClick={() => withBusy(u.id, () => reinstateUser(u.id))}
                        >
                          Reinstate
                        </button>
                      )}
                      {u.status !== "suspended" && !isSelf && (
                        <button
                          className="btn ghost"
                          disabled={busy}
                          onClick={() => withBusy(u.id, () => suspendUser(u.id))}
                        >
                          Suspend
                        </button>
                      )}
                      {u.role === "user" ? (
                        <button
                          className="btn ghost"
                          disabled={busy}
                          onClick={() => withBusy(u.id, () => promoteUser(u.id))}
                        >
                          Make admin
                        </button>
                      ) : (
                        !isSelf && (
                          <button
                            className="btn ghost"
                            disabled={busy}
                            onClick={() => withBusy(u.id, () => demoteUser(u.id))}
                          >
                            Revoke admin
                          </button>
                        )
                      )}
                      {u.role !== "superadmin" && !isSelf && (
                        <button className="btn ghost admin-delete-btn" disabled={busy} onClick={() => handleDelete(u)}>
                          Delete
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="section-header" style={{ marginTop: 32 }}>
        <h2>Access codes</h2>
        <span className="meta">Generate a code once payment is confirmed outside the platform</span>
      </div>

      {codes && (
        <div className="admin-overview" style={{ marginBottom: 20 }}>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{codeStats.active}</span>
            <span className="admin-overview-label">Active codes</span>
          </div>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{codeStats.exhausted}</span>
            <span className="admin-overview-label">Exhausted</span>
          </div>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{codeStats.expired}</span>
            <span className="admin-overview-label">Expired</span>
          </div>
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{codeStats.revoked}</span>
            <span className="admin-overview-label">Revoked</span>
          </div>
        </div>
      )}

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        {justCreated && (
          <div className="status-result-explainer" style={{ marginBottom: 16 }}>
            <h3>{justCreatedWasResend ? "Code resent" : "Code generated -- shown once"}</h3>
            <p style={{ fontFamily: "monospace", fontSize: 18, letterSpacing: 1 }}>{justCreated.code}</p>
            {justCreated.emailed ? (
              <p style={{ margin: 0 }}>
                Emailed to <strong>{justCreated.assigned_email}</strong>. Copy it anyway -- every later
                view shows it masked.
              </p>
            ) : (
              <>
                <p style={{ margin: 0 }}>Copy it now -- every later view shows it masked.</p>
                {/* The code is valid either way; a failed send must not read as a failed
                    generation, or an admin will reissue a code that was never broken. */}
                {justCreated.email_error && (
                  <p style={{ margin: "8px 0 0", color: "var(--warning, #c98a00)" }}>
                    Not emailed: {justCreated.email_error} Send it to them yourself.
                  </p>
                )}
              </>
            )}
          </div>
        )}

        <form onSubmit={handleCreateCode} className="auth-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
          <label>
            Duration
            <select value={durationPreset} onChange={(e) => handleDurationPresetChange(e.target.value)}>
              {DURATION_PRESETS.map((d) => (
                <option key={d} value={d}>
                  {d} day{d === 1 ? "" : "s"}
                </option>
              ))}
              <option value="custom">Custom...</option>
            </select>
          </label>
          {durationPreset === "custom" && (
            <label>
              Custom duration (days)
              <input type="number" min={1} required value={durationDays} onChange={(e) => setDurationDays(e.target.value)} />
            </label>
          )}
          <label>
            Member's email
            <input
              type="email"
              required
              value={assignedEmail}
              onChange={(e) => setAssignedEmail(e.target.value)}
              placeholder="user@example.com"
              list="member-email-options"
            />
            {/* A dropdown of registered emails to pick from, not a hard
                constraint -- a code is routinely generated for someone who
                hasn't registered yet (see Pricing.tsx: pay first, register
                whenever), so free typing still has to work for an email
                this list doesn't have. */}
            <datalist id="member-email-options">
              {(users ?? []).map((u) => (
                <option key={u.id} value={u.email}>
                  {u.name ? `${u.name} -- ${u.email}` : u.email}
                </option>
              ))}
            </datalist>
          </label>
          <label>
            Notes <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>(optional -- e.g. payment reference)</span>
            <input value={codeNotes} onChange={(e) => setCodeNotes(e.target.value)} placeholder="MOMO-XXXXXXX" />
          </label>
          <label style={{ display: "flex", alignItems: "flex-end", gap: 8, fontWeight: 400 }}>
            <input
              type="checkbox"
              checked={sendByEmail}
              onChange={(e) => setSendByEmail(e.target.checked)}
              style={{ width: "auto", margin: 0 }}
            />
            Email it to them
          </label>
          <div style={{ display: "flex", alignItems: "flex-end" }}>
            <button className="btn" type="submit" disabled={codeBusy}>
              {codeBusy ? "Generating..." : "Generate code"}
            </button>
          </div>
        </form>
        {codeError && <p className="auth-error" style={{ marginTop: 12 }}>{codeError}</p>}
      </div>

      <div className="admin-header" style={{ marginBottom: 12 }}>
        <input
          value={codeSearch}
          onChange={(e) => setCodeSearch(e.target.value)}
          placeholder="Search by code, email or notes..."
          aria-label="Search access codes"
          style={{ maxWidth: 260 }}
          className="admin-ref-input"
        />
        <div className="admin-filters">
          {CODE_FILTERS.map((f) => (
            <button
              key={f.value}
              className={`btn ghost${codeFilter === f.value ? " active" : ""}`}
              onClick={() => setCodeFilter(f.value)}
              aria-pressed={codeFilter === f.value}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="card admin-table-wrapper" style={{ marginBottom: 32 }}>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Redeemed</th>
              <th>Assigned</th>
              <th>Notes</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {codes === null && (
              <tr>
                <td colSpan={8}>Loading...</td>
              </tr>
            )}
            {codes !== null && visibleCodes.length === 0 && (
              <tr>
                <td colSpan={8}>{codes.length === 0 ? "No access codes generated yet." : "No codes match this filter."}</td>
              </tr>
            )}
            {visibleCodes.map((c) => (
              <tr key={c.id}>
                <td style={{ fontFamily: "monospace" }}>{revealed[c.id] ?? c.code}</td>
                <td>
                  <span className={`status-tag ${CODE_TONE[c.status]}`}>{c.status}</span>
                </td>
                <td>{c.duration_days}d</td>
                <td>
                  {c.redemption_count} / {c.redemption_limit}
                </td>
                <td>{c.assigned_email ?? "--"}</td>
                <td>{c.notes || "--"}</td>
                <td>{new Date(c.created_at).toLocaleDateString()}</td>
                <td style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button className="btn ghost" onClick={() => toggleReveal(c.id)}>
                    {c.id in revealed ? "Hide" : "Reveal"}
                  </button>
                  {c.status === "active" && c.assigned_email && (
                    <button className="btn ghost" disabled={codeBusy} onClick={() => handleResendCode(c.id)}>
                      Resend
                    </button>
                  )}
                  {c.status === "active" && (
                    <button className="btn ghost" disabled={codeBusy} onClick={() => handleRevokeCode(c.id)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="admin-audit">
        <Link className="btn ghost" to="/app/admin/activity-log">
          View full activity log
        </Link>
        <p className="admin-audit-note">
          Every sign-in, registration, status and settings change, access-code and curated-pick
          action, and generated booking code -- filterable by user, action and date, on its own page.
        </p>
      </div>
    </div>
  );
}
