import { useEffect, useState } from "react";
import { createBannerItem, deleteBannerItem, fetchBannerItems, updateBannerItem } from "../api";
import { ErrorState } from "../components/ErrorState";
import type { BannerItem } from "../types";

interface FormState {
  sponsor: string;
  headline: string;
  body: string;
  cta_label: string;
  link_url: string;
  starts_at: string; // datetime-local, in the admin's own time zone
  ends_at: string;
  sort_order: string;
  active: boolean;
}

const EMPTY: FormState = {
  sponsor: "", headline: "", body: "", cta_label: "Learn more", link_url: "", starts_at: "", ends_at: "", sort_order: "0", active: true,
};

/** The backend sends naive UTC; a <input type="datetime-local"> wants local time. */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toIso(local: string): string | null {
  return local ? new Date(local).toISOString() : null;
}

function describe(item: BannerItem): { label: string; tone: string } {
  if (item.live) return { label: "Live", tone: "active" };
  if (!item.active) return { label: "Off", tone: "pending" };
  const now = Date.now();
  const asUtc = (s: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`).getTime();
  if (item.starts_at && asUtc(item.starts_at) > now) return { label: "Scheduled", tone: "pending" };
  if (item.ends_at && asUtc(item.ends_at) <= now) return { label: "Ended", tone: "suspended" };
  return { label: "Not shown", tone: "suspended" };
}

/**
 * Admin -> Banner & ads. The news banner's sponsored slides: a sponsor's name,
 * a headline, an optional line, a button label and an https link, shown
 * between the football headlines for as long as the dates allow. They are
 * always labelled "Sponsored" to members and are text only.
 */
export function AdminBanner() {
  const [items, setItems] = useState<BannerItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  function load() {
    setError(null);
    fetchBannerItems()
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }

  useEffect(load, []);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function startEdit(item: BannerItem) {
    setEditing(item.id);
    setForm({
      sponsor: item.sponsor, headline: item.headline, body: item.body ?? "", cta_label: item.cta_label,
      link_url: item.link_url, starts_at: toLocalInput(item.starts_at), ends_at: toLocalInput(item.ends_at),
      sort_order: String(item.sort_order), active: item.active,
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function reset() {
    setEditing(null);
    setForm(EMPTY);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const base = {
      sponsor: form.sponsor.trim(), headline: form.headline.trim(), body: form.body.trim() || null,
      cta_label: form.cta_label.trim() || "Learn more", link_url: form.link_url.trim(),
      active: form.active, sort_order: Number(form.sort_order) || 0,
    };
    try {
      if (editing === null) {
        await createBannerItem({ ...base, starts_at: toIso(form.starts_at), ends_at: toIso(form.ends_at) });
      } else {
        await updateBannerItem(editing, {
          ...base,
          starts_at: toIso(form.starts_at), ends_at: toIso(form.ends_at),
          clear_starts_at: !form.starts_at, clear_ends_at: !form.ends_at,
        });
      }
      reset();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (error && items === null) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="section-header">
        <h2>Banner &amp; ads</h2>
        <span className="meta">Sponsored slides shown between the football headlines</span>
      </div>

      <form className="card card-pad auth-form" onSubmit={submit} style={{ maxWidth: 640, marginBottom: 24 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>{editing === null ? "New sponsored slide" : "Edit sponsored slide"}</h3>
        <label>
          Sponsor name (shown on the slide)
          <input required maxLength={80} value={form.sponsor} onChange={(e) => set("sponsor", e.target.value)} />
        </label>
        <label>
          Headline
          <input required maxLength={140} value={form.headline} onChange={(e) => set("headline", e.target.value)} />
        </label>
        <label>
          Extra line (optional -- shown on hover)
          <input maxLength={200} value={form.body} onChange={(e) => set("body", e.target.value)} />
        </label>
        <label>
          Button text
          <input required maxLength={40} value={form.cta_label} onChange={(e) => set("cta_label", e.target.value)} />
        </label>
        <label>
          Link (must start with https://)
          <input required type="url" maxLength={500} placeholder="https://" value={form.link_url} onChange={(e) => set("link_url", e.target.value)} />
        </label>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <label style={{ flex: 1, minWidth: 180 }}>
            Starts (optional)
            <input type="datetime-local" value={form.starts_at} onChange={(e) => set("starts_at", e.target.value)} />
          </label>
          <label style={{ flex: 1, minWidth: 180 }}>
            Ends (optional)
            <input type="datetime-local" value={form.ends_at} onChange={(e) => set("ends_at", e.target.value)} />
          </label>
          <label style={{ width: 110 }}>
            Order
            <input type="number" min={-1000} max={1000} value={form.sort_order} onChange={(e) => set("sort_order", e.target.value)} />
          </label>
        </div>
        <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={form.active} onChange={(e) => set("active", e.target.checked)} />
          Switched on
        </label>
        {error && <p className="auth-error">{error}</p>}
        <div style={{ display: "flex", gap: 10 }}>
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Saving..." : editing === null ? "Add slide" : "Save changes"}
          </button>
          {editing !== null && (
            <button className="btn ghost" type="button" onClick={reset}>Cancel</button>
          )}
        </div>
        <p className="meta" style={{ margin: 0 }}>
          Members see this as a "Sponsored" slide, one for every three headlines. Text only: images from other sites
          can't load on this site.
        </p>
      </form>

      {items === null && <p className="badge-neutral">Loading…</p>}
      {items?.length === 0 && <p className="sub">No sponsored slides yet.</p>}
      <div style={{ display: "grid", gap: 12 }}>
        {items?.map((item) => {
          const status = describe(item);
          const ctr = item.impressions > 0 ? `${((item.clicks / item.impressions) * 100).toFixed(1)}%` : "--";
          return (
            <div key={item.id} className="card card-pad" style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <span className={`status-tag ${status.tone}`}>{status.label}</span>
                <strong>{item.sponsor}</strong>
                <span>{item.headline}</span>
              </div>
              <div className="sub" style={{ wordBreak: "break-all" }}>
                {item.cta_label} → <a href={item.link_url} target="_blank" rel="noopener noreferrer">{item.link_url}</a>
              </div>
              <div className="sub">
                {item.impressions.toLocaleString()} views · {item.clicks.toLocaleString()} clicks · {ctr} click rate
                {item.starts_at && ` · from ${new Date(`${item.starts_at}Z`).toLocaleString()}`}
                {item.ends_at && ` · until ${new Date(`${item.ends_at}Z`).toLocaleString()}`}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn ghost" onClick={() => startEdit(item)}>Edit</button>
                <button className="btn ghost" onClick={() => act(() => updateBannerItem(item.id, { active: !item.active }))}>
                  {item.active ? "Switch off" : "Switch on"}
                </button>
                <button
                  className="btn ghost admin-delete-btn"
                  onClick={() => {
                    if (window.confirm(`Delete the slide from ${item.sponsor}? Its view and click counts go with it.`)) {
                      void act(() => deleteBannerItem(item.id));
                    }
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
