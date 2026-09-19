import { useEffect, useState } from "react";
import {
  createAccessCode,
  demoteUser,
  extendUserAccess,
  fetchAccessCodes,
  fetchAdminOverview,
  fetchAdminUsers,
  fetchAuditLog,
  fetchSettings,
  promoteUser,
  reinstateUser,
  resendAccessCode,
  revealAccessCode,
  revokeAccessCode,
  revokeUserAccess,
  suspendUser,
} from "../api";
import { useAuth } from "../lib/AuthContext";
import type { AccessCode, AdminOverview, AdminUser, AuditLogEntry, UserStatus } from "../types";
import { ErrorState } from "../components/ErrorState";

const FILTERS: { label: string; value: UserStatus | "all" }[] = [
  { label: "Active", value: "active" },
  { label: "Suspended", value: "suspended" },
  { label: "All", value: "all" },
];

const ACTION_LABELS: Record<string, string> = {
  "account.registered": "registered",
  "user.suspended": "suspended",
  "user.reinstated": "reinstated",
  "user.promoted": "promoted to admin",
  "user.demoted": "admin revoked",
  "access_code.created": "access code generated",
  "access_code.revoked": "access code revoked",
  "access_code.redeemed": "access code redeemed",
  "access_grant.extended": "access extended",
  "access_grant.revoked": "access revoked",
};

const ACCESS_TONE: Record<AdminUser["access_status"], string> = {
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
  const [audit, setAudit] = useState<AuditLogEntry[]>([]);
  const [showAudit, setShowAudit] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

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

  useEffect(() => {
    if (!showAudit) return;
    fetchAuditLog(40).then(setAudit).catch(() => setAudit([]));
  }, [showAudit, users]);

  async function withBusy(id: number, action: () => Promise<AdminUser>) {
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

  async function handleRevokeAccess(id: number) {
    await withBusy(id, () => revokeUserAccess(id));
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
            {users !== null && users.length === 0 && <tr><td colSpan={7}>No users in this view.</td></tr>}
            {users?.map((u) => {
              const isSelf = u.id === currentUser?.id;
              const busy = busyId === u.id;
              return (
                <tr key={u.id}>
                  <td>{u.name}</td>
                  <td>{u.email}</td>
                  <td>
                    <span className={`status-tag ${u.status}`}>{u.status}</span>
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
                  </td>
                  <td>{new Date(u.created_at).toLocaleDateString()}</td>
                  <td>
                    <div className="admin-actions">
                      {u.access_status !== "none" && (
                        <button className="btn ghost" disabled={busy} onClick={() => handleExtend(u.id)}>
                          Extend
                        </button>
                      )}
                      {u.access_status !== "none" && (
                        <button className="btn ghost" disabled={busy} onClick={() => handleRevokeAccess(u.id)}>
                          Revoke access
                        </button>
                      )}
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
            />
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
        <button className="btn ghost" onClick={() => setShowAudit(!showAudit)} aria-expanded={showAudit}>
          {showAudit ? "Hide" : "Show"} access audit log
        </button>
        {showAudit && (
          <>
            <p className="admin-audit-note">
              Append-only record of every registration, status change and access-code action. A user or
              code row only ever shows its current state -- this preserves the full sequence.
            </p>
            <div className="card admin-table-wrapper">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Target user</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.length === 0 && (
                    <tr>
                      <td colSpan={5}>No entries recorded yet.</td>
                    </tr>
                  )}
                  {audit.map((entry) => (
                    <tr key={entry.id}>
                      <td>{new Date(entry.created_at).toLocaleString()}</td>
                      <td>{entry.actor_email ?? "--"}</td>
                      <td>{ACTION_LABELS[entry.action] ?? entry.action}</td>
                      <td>{entry.target_user_id ?? "--"}</td>
                      <td className="admin-audit-detail">
                        {entry.detail
                          ? Object.entries(entry.detail)
                              .filter(([, v]) => v !== null && v !== "")
                              .map(([k, v]) => `${k}: ${v}`)
                              .join(", ") || "--"
                          : "--"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
