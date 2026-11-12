import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchAdminPayments, recheckPayment } from "../api";
import type { AdminPayment } from "../types";
import { ErrorState } from "../components/ErrorState";

const STATUS_LABEL: Record<AdminPayment["status"], string> = {
  pending: "Waiting for payment",
  fulfilling: "Paid, adding access",
  completed: "Paid",
  cancelled: "Cancelled",
  failed: "Failed",
};

const STATUS_TONE: Record<AdminPayment["status"], string> = {
  pending: "pending",
  fulfilling: "pending",
  completed: "active",
  cancelled: "suspended",
  failed: "suspended",
};

const FILTERS: { value: string; label: string }[] = [
  { value: "all", label: "All payments" },
  { value: "completed", label: "Paid" },
  { value: "pending", label: "Waiting for payment" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

function money(amount: number): string {
  return `GH₵${amount.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "--";
}

/**
 * Every online payment taken through Hubtel, newest first: who paid, what
 * for, how much, and where it stands. Built so a payment can be shown to
 * (and debugged with) Hubtel's engineers -- "What Hubtel said" is the raw
 * answer Hubtel's status check gave, and "Check with Hubtel" asks again and
 * finishes the job if the payment turns out to have gone through.
 */
export function AdminPayments() {
  const [payments, setPayments] = useState<AdminPayment[] | null>(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchAdminPayments(filter === "all" ? undefined : filter)
      .then(setPayments)
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
  }, [filter]);

  useEffect(() => {
    setPayments(null);
    load();
  }, [load]);

  async function check(payment: AdminPayment) {
    setBusy(payment.id);
    setNotice(null);
    try {
      const updated = await recheckPayment(payment.id);
      setPayments((rows) => (rows ?? []).map((r) => (r.id === updated.id ? updated : r)));
      setOpen(updated.id);
      setNotice(
        updated.status === payment.status
          ? `Hubtel says this payment is still "${STATUS_LABEL[updated.status].toLowerCase()}".`
          : `Updated: ${payment.reference} is now "${STATUS_LABEL[updated.status].toLowerCase()}".`
      );
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Couldn't reach Hubtel. Try again in a moment.");
    } finally {
      setBusy(null);
    }
  }

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (payments ?? []).filter(
      (p) =>
        !q ||
        p.reference.toLowerCase().includes(q) ||
        (p.user_email ?? "").toLowerCase().includes(q) ||
        p.product_label.toLowerCase().includes(q)
    );
  }, [payments, search]);

  const paidTotal = visible.filter((p) => p.status === "completed").reduce((sum, p) => sum + p.amount, 0);

  if (error) return <ErrorState message={error} />;

  return (
    <div>
      <div className="section-header">
        <h2>Payments</h2>
        <span className="meta">
          Every online payment through Hubtel. Open a row to see exactly what Hubtel told us. If a
          customer says they paid but have no access, press "Check with Hubtel".
        </span>
      </div>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div
          className="auth-form"
          style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}
        >
          <label>
            Show
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              {FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Search
            <input
              type="search"
              placeholder="Reference, email or what was bought"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
        {payments !== null && (
          <p className="meta" style={{ margin: "12px 0 0" }}>
            {visible.length} payment{visible.length === 1 ? "" : "s"} shown. Paid in this list: {money(paidTotal)}.
          </p>
        )}
        {notice && (
          <p role="status" className="setting-note" style={{ margin: "10px 0 0" }}>
            {notice}
          </p>
        )}
      </div>

      <div className="card admin-table-wrapper">
        <table className="admin-table payments-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Customer</th>
              <th>Bought</th>
              <th>Amount</th>
              <th>Status</th>
              <th>Reference</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {payments === null && (
              <tr>
                <td colSpan={7}>Loading...</td>
              </tr>
            )}
            {payments !== null && visible.length === 0 && (
              <tr>
                <td colSpan={7}>No payments to show.</td>
              </tr>
            )}
            {visible.map((p) => {
              const isOpen = open === p.id;
              const canCheck = p.status === "pending";
              return [
                <tr key={p.id}>
                  <td data-label="Date">{when(p.created_at)}</td>
                  <td data-label="Customer">{p.user_email ?? "--"}</td>
                  <td data-label="Bought">{p.product_label}</td>
                  <td data-label="Amount">{money(p.amount)}</td>
                  <td data-label="Status">
                    <span className={`status-tag ${STATUS_TONE[p.status]}`}>{STATUS_LABEL[p.status]}</span>
                  </td>
                  <td data-label="Reference">
                    <code>{p.reference}</code>
                  </td>
                  <td className="payments-actions">
                    {canCheck && (
                      <button className="btn" disabled={busy === p.id} onClick={() => check(p)}>
                        {busy === p.id ? "Checking..." : "Check with Hubtel"}
                      </button>
                    )}{" "}
                    <button
                      className="btn ghost"
                      aria-expanded={isOpen}
                      onClick={() => setOpen(isOpen ? null : p.id)}
                    >
                      {isOpen ? "Hide details" : "Details"}
                    </button>
                  </td>
                </tr>,
                isOpen && (
                  <tr key={`${p.id}-detail`}>
                    <td colSpan={7} className="payments-detail-cell">
                      <dl className="payment-detail">
                        <div>
                          <dt>Started</dt>
                          <dd>{when(p.created_at)}</dd>
                        </div>
                        <div>
                          <dt>Paid / finished</dt>
                          <dd>{when(p.completed_at)}</dd>
                        </div>
                        <div>
                          <dt>Last asked Hubtel</dt>
                          <dd>{p.last_checked_at ? when(p.last_checked_at) : "Not yet"}</dd>
                        </div>
                      </dl>
                      <strong>What Hubtel said</strong>
                      {p.provider_detail ? (
                        <pre className="payment-raw">{JSON.stringify(p.provider_detail, null, 2)}</pre>
                      ) : (
                        <p className="meta" style={{ margin: "4px 0 0" }}>
                          Nothing yet. We only record Hubtel's answer when we ask it about this payment.
                        </p>
                      )}
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
