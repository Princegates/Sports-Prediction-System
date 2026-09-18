import { useEffect, useState } from "react";
import {
  createAccessCode,
  demoteUser,
  extendUserAccess,
  fetchAccessCodes,
  fetchAdminOverview,
  fetchAdminUsers,
  fetchAuditLog,
  promoteUser,
  reinstateUser,
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
  const [justCreated, setJustCreated] = useState<AccessCode | null>(null);
  const [durationDays, setDurationDays] = useState("30");
  const [redemptionLimit, setRedemptionLimit] = useState("1");
  const [assignedEmail, setAssignedEmail] = useState("");
  const [codeNotes, setCodeNotes] = useState("");

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
        redemption_limit: Number(redemptionLimit) || 1,
        assigned_user_email: assignedEmail.trim() || undefined,
        notes: codeNotes.trim() || undefined,
      });
      setJustCreated(created);
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

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        {justCreated && (
          <div className="status-result-explainer" style={{ marginBottom: 16 }}>
            <h3>Code generated -- shown once</h3>
            <p style={{ fontFamily: "monospace", fontSize: 18, letterSpacing: 1 }}>{justCreated.code}</p>
            <p style={{ margin: 0 }}>Copy it now -- every later view shows it masked.</p>
          </div>
        )}

        <form onSubmit={handleCreateCode} className="auth-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
          <label>
            Duration (days)
            <input type="number" min={1} required value={durationDays} onChange={(e) => setDurationDays(e.target.value)} />
          </label>
          <label>
            Redemption limit
            <input type="number" min={1} required value={redemptionLimit} onChange={(e) => setRedemptionLimit(e.target.value)} />
          </label>
          <label>
            Assign to email <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>(optional)</span>
            <input type="email" value={assignedEmail} onChange={(e) => setAssignedEmail(e.target.value)} placeholder="user@example.com" />
          </label>
          <label>
            Notes <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>(optional -- e.g. payment reference)</span>
            <input value={codeNotes} onChange={(e) => setCodeNotes(e.target.value)} placeholder="MOMO-XXXXXXX" />
          </label>
          <div style={{ display: "flex", alignItems: "flex-end" }}>
            <button className="btn" type="submit" disabled={codeBusy}>
              {codeBusy ? "Generating..." : "Generate code"}
            </button>
          </div>
        </form>
        {codeError && <p className="auth-error" style={{ marginTop: 12 }}>{codeError}</p>}
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
            {codes !== null && codes.length === 0 && (
              <tr>
                <td colSpan={8}>No access codes generated yet.</td>
              </tr>
            )}
            {codes?.map((c) => (
              <tr key={c.id}>
                <td style={{ fontFamily: "monospace" }}>{c.code}</td>
                <td>
                  <span className={`status-tag ${c.status === "active" ? "active" : c.status === "revoked" ? "suspended" : "pending"}`}>
                    {c.status}
                  </span>
                </td>
                <td>{c.duration_days}d</td>
                <td>
                  {c.redemption_count} / {c.redemption_limit}
                </td>
                <td>{c.assigned_user_id ?? "anyone"}</td>
                <td>{c.notes || "--"}</td>
                <td>{new Date(c.created_at).toLocaleDateString()}</td>
                <td>
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
