import { useEffect, useMemo, useState } from "react";
import { fetchAdminUsers, fetchAuditLog } from "../api";
import type { AdminUser, AuditLogEntry } from "../types";
import { ErrorState } from "../components/ErrorState";

export const ACTION_LABELS: Record<string, string> = {
  "account.registered": "registered",
  "account.login": "signed in",
  "account.login_failed": "sign-in failed",
  "account.login_blocked": "sign-in blocked (suspended)",
  "account.password_changed": "password changed",
  "account.profile_updated": "profile updated",
  "user.suspended": "suspended",
  "user.reinstated": "reinstated",
  "user.promoted": "promoted to admin",
  "user.demoted": "admin revoked",
  "access_code.created": "access code generated",
  "access_code.revoked": "access code revoked",
  "access_code.redeemed": "access code redeemed",
  "access_code.redeem_failed": "access code redemption failed",
  "access_code.resent": "access code resent",
  "access_code.revealed": "access code revealed",
  "access_grant.extended": "access extended",
  "access_grant.revoked": "access revoked",
  "referral.bonus_granted": "referral bonus granted",
  "booking_code.generated": "booking code generated",
  "settings.updated": "settings changed",
  "match.live_cleared": "live events cleared",
  "featured_pick.created": "guda pick created",
  "featured_pick.updated": "guda pick updated",
  "featured_pick.removed": "guda pick removed",
  "featured_pick.result_set": "guda pick result set",
  "admin_pick.created": "admin pick created",
  "admin_pick.updated": "admin pick updated",
  "admin_pick.removed": "admin pick removed",
  "admin_pick.result_set": "admin pick result set",
};

const PAGE_SIZE = 50;

/** The full audit trail, filtered and paginated server-side -- the record
 * AdminUsers' old embedded widget only ever showed the most recent 100 rows
 * of, unfiltered. ``user_id`` filters by either side of a row (actor or
 * target -- see the backend endpoint's own docstring for why that's one
 * filter, not two), so picking a user shows both what they did and what was
 * done to them.
 *
 * Two new action types live here alongside the long-standing ones:
 * access_code.redeem_failed (a rejected redemption attempt -- invalid,
 * expired, already used, or at its limit) and booking_code.generated
 * (every AI Generation booking-code request) -- usage activity that
 * previously left no trace at all.
 */
export function ActivityLog() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [userId, setUserId] = useState("all");
  const [action, setAction] = useState("all");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAdminUsers().then(setUsers).catch(() => setUsers([]));
  }, []);

  useEffect(() => {
    setError(null);
    // "until" is a day picked on a calendar, not a timestamp -- rolling it
    // forward to the start of the next day makes the filter inclusive of
    // the whole day the admin selected, matching what "to <date>" implies.
    const untilInclusive = until
      ? new Date(new Date(until).getTime() + 24 * 60 * 60 * 1000).toISOString()
      : undefined;
    fetchAuditLog({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      userId: userId === "all" ? undefined : Number(userId),
      action: action === "all" ? undefined : action,
      since: since ? new Date(since).toISOString() : undefined,
      until: untilInclusive,
    })
      .then((p) => {
        setEntries(p.items);
        setTotal(p.total);
      })
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
  }, [page, userId, action, since, until]);

  const userEmailById = useMemo(() => {
    const map = new Map<number, string>();
    (users ?? []).forEach((u) => map.set(u.id, u.email));
    return map;
  }, [users]);

  function targetLabel(entry: AuditLogEntry): string {
    if (entry.target_user_id == null) return "--";
    return userEmailById.get(entry.target_user_id) ?? String(entry.target_user_id);
  }

  const visibleEntries = (entries ?? []).filter((entry) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      (entry.actor_email ?? "").toLowerCase().includes(q) ||
      targetLabel(entry).toLowerCase().includes(q) ||
      (ACTION_LABELS[entry.action] ?? entry.action).toLowerCase().includes(q)
    );
  });

  const actionOptions = Object.keys(ACTION_LABELS).sort((a, b) =>
    ACTION_LABELS[a].localeCompare(ACTION_LABELS[b])
  );
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function changeFilter(fn: () => void) {
    fn();
    setPage(0);
  }

  if (error) return <ErrorState message={error} />;

  return (
    <div>
      <div className="section-header">
        <h2>Activity Log</h2>
        <span className="meta">
          Append-only record of sign-ins, registrations, status and settings changes, every
          access-code and curated-pick action, and every generated booking code -- filtered and
          paginated, not just the latest few hundred rows.
        </span>
      </div>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div
          className="auth-form"
          style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}
        >
          <label>
            User
            <select value={userId} onChange={(e) => changeFilter(() => setUserId(e.target.value))}>
              <option value="all">Everyone</option>
              {(users ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email}
                </option>
              ))}
            </select>
          </label>
          <label>
            Action
            <select value={action} onChange={(e) => changeFilter(() => setAction(e.target.value))}>
              <option value="all">Every action</option>
              {actionOptions.map((a) => (
                <option key={a} value={a}>
                  {ACTION_LABELS[a]}
                </option>
              ))}
            </select>
          </label>
          <label>
            From
            <input type="date" value={since} onChange={(e) => changeFilter(() => setSince(e.target.value))} />
          </label>
          <label>
            To
            <input type="date" value={until} onChange={(e) => changeFilter(() => setUntil(e.target.value))} />
          </label>
          <label>
            Search this page
            <input
              type="search"
              placeholder="Actor, target, or action"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
      </div>

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
            {entries === null && (
              <tr>
                <td colSpan={5}>Loading...</td>
              </tr>
            )}
            {entries !== null && visibleEntries.length === 0 && (
              <tr>
                <td colSpan={5}>No entries match these filters.</td>
              </tr>
            )}
            {visibleEntries.map((entry) => (
              <tr key={entry.id}>
                <td>{new Date(entry.created_at).toLocaleString()}</td>
                <td>{entry.actor_email ?? "--"}</td>
                <td>{ACTION_LABELS[entry.action] ?? entry.action}</td>
                <td>{targetLabel(entry)}</td>
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

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
        <span className="meta">
          {total === 0 ? "No entries" : `${page * PAGE_SIZE + 1}-${Math.min(total, (page + 1) * PAGE_SIZE)} of ${total}`}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn ghost" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
            Previous
          </button>
          <button className="btn ghost" disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}>
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
