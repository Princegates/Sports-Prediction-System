import { useEffect, useState } from "react";
import { approveUser, demoteUser, fetchAdminUsers, promoteUser, suspendUser } from "../api";
import { useAuth } from "../lib/AuthContext";
import type { AdminUser, UserStatus } from "../types";
import { ErrorState } from "../components/ErrorState";

const FILTERS: { label: string; value: UserStatus | "all" }[] = [
  { label: "Pending", value: "pending" },
  { label: "Active", value: "active" },
  { label: "Suspended", value: "suspended" },
  { label: "All", value: "all" },
];

export function AdminUsers() {
  const { user: currentUser } = useAuth();
  const [filter, setFilter] = useState<UserStatus | "all">("pending");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  function load() {
    setError(null);
    fetchAdminUsers(filter === "all" ? undefined : filter)
      .then(setUsers)
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
  }

  useEffect(() => {
    setUsers(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

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

  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>User access</h1>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0" }}>
            Approve new registrations after payment confirmation, and manage account access.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
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
                <td colSpan={7}>No users in this view.</td>
              </tr>
            )}
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
                  <td>{u.payment_reference ?? "--"}</td>
                  <td>{new Date(u.created_at).toLocaleDateString()}</td>
                  <td>
                    <div className="admin-actions">
                      {u.status === "pending" && (
                        <button className="btn" disabled={busy} onClick={() => withBusy(u.id, () => approveUser(u.id, u.payment_reference ?? undefined))}>
                          Approve
                        </button>
                      )}
                      {u.status !== "suspended" && !isSelf && (
                        <button className="btn ghost" disabled={busy} onClick={() => withBusy(u.id, () => suspendUser(u.id))}>
                          Suspend
                        </button>
                      )}
                      {u.status === "suspended" && (
                        <button className="btn" disabled={busy} onClick={() => withBusy(u.id, () => approveUser(u.id, u.payment_reference ?? undefined))}>
                          Reactivate
                        </button>
                      )}
                      {u.role === "user" ? (
                        <button className="btn ghost" disabled={busy} onClick={() => withBusy(u.id, () => promoteUser(u.id))}>
                          Make admin
                        </button>
                      ) : (
                        !isSelf && (
                          <button className="btn ghost" disabled={busy} onClick={() => withBusy(u.id, () => demoteUser(u.id))}>
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
    </div>
  );
}
