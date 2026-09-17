import { useEffect, useState } from "react";
import {
  approveUser,
  demoteUser,
  fetchAdminOverview,
  fetchAdminUsers,
  fetchAuditLog,
  promoteUser,
  reinstateUser,
  suspendUser,
} from "../api";
import { useAuth } from "../lib/AuthContext";
import type { AdminOverview, AdminUser, AuditLogEntry, UserStatus } from "../types";
import { ErrorState } from "../components/ErrorState";

const FILTERS: { label: string; value: UserStatus | "all" }[] = [
  { label: "Pending", value: "pending" },
  { label: "Active", value: "active" },
  { label: "Suspended", value: "suspended" },
  { label: "All", value: "all" },
];

const ACTION_LABELS: Record<string, string> = {
  "account.registered": "registered",
  "user.approved": "approved",
  "user.suspended": "suspended",
  "user.reinstated": "reinstated",
  "user.promoted": "promoted to admin",
  "user.demoted": "admin revoked",
};

export function AdminUsers() {
  const { user: currentUser } = useAuth();
  const [filter, setFilter] = useState<UserStatus | "all">("pending");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [audit, setAudit] = useState<AuditLogEntry[]>([]);
  const [showAudit, setShowAudit] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  // Per-row override so an admin can correct or add a reference at the moment
  // of approval, instead of approving a blank one and editing the DB later.
  const [refDraft, setRefDraft] = useState<Record<number, string>>({});

  function load() {
    setError(null);
    fetchAdminUsers(filter === "all" ? undefined : filter)
      .then(setUsers)
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
    // The overview is refreshed alongside the list so the queue badge can't
    // sit stale after an approval.
    fetchAdminOverview().then(setOverview).catch(() => setOverview(null));
  }

  useEffect(() => {
    setUsers(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

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

  if (error && users === null) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="admin-header">
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>User access</h1>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0" }}>
            Nobody can use this system until you approve them. Confirm the payment reference out of
            band, then approve.
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
              {f.value === "pending" && overview && overview.pending_users > 0 && (
                <span className="queue-badge">{overview.pending_users}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {overview && (
        <div className={`admin-overview${overview.pending_users > 0 ? " has-queue" : ""}`}>
          <div className="admin-overview-tile primary">
            <span className="admin-overview-value">{overview.pending_users}</span>
            <span className="admin-overview-label">
              {overview.pending_users === 1 ? "account awaiting" : "accounts awaiting"} your approval
            </span>
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
          <div className="admin-overview-tile">
            <span className="admin-overview-value">{overview.chat_messages.toLocaleString()}</span>
            <span className="admin-overview-label">Assistant messages</span>
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
              <th>Payment reference</th>
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
            {users !== null && users.length === 0 && (
              <tr>
                <td colSpan={7}>
                  {filter === "pending"
                    ? "Nothing waiting — the approval queue is clear."
                    : "No users in this view."}
                </td>
              </tr>
            )}
            {users?.map((u) => {
              const isSelf = u.id === currentUser?.id;
              const busy = busyId === u.id;
              const draft = refDraft[u.id] ?? u.payment_reference ?? "";
              return (
                <tr key={u.id}>
                  <td>{u.name}</td>
                  <td>{u.email}</td>
                  <td>
                    <span className={`status-tag ${u.status}`}>{u.status}</span>
                  </td>
                  <td>{u.role}</td>
                  <td>
                    {u.status === "pending" ? (
                      <input
                        className="admin-ref-input"
                        value={draft}
                        placeholder="MOMO-..."
                        onChange={(e) => setRefDraft({ ...refDraft, [u.id]: e.target.value })}
                        aria-label={`Payment reference for ${u.email}`}
                      />
                    ) : (
                      u.payment_reference || "--"
                    )}
                  </td>
                  <td>{new Date(u.created_at).toLocaleDateString()}</td>
                  <td>
                    <div className="admin-actions">
                      {u.status === "pending" && (
                        <button
                          className="btn"
                          disabled={busy}
                          onClick={() => withBusy(u.id, () => approveUser(u.id, draft || undefined))}
                        >
                          {busy ? "..." : "Approve"}
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

      <div className="admin-audit">
        <button className="btn ghost" onClick={() => setShowAudit(!showAudit)} aria-expanded={showAudit}>
          {showAudit ? "Hide" : "Show"} access audit log
        </button>
        {showAudit && (
          <>
            <p className="admin-audit-note">
              Append-only record of every registration and privileged action. Kept because{" "}
              <code>approved_by</code> on the account itself is overwritten by the next status change —
              this preserves the sequence.
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
