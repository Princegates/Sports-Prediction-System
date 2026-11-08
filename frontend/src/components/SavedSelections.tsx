import { useEffect, useState } from "react";
import { deleteSavedSelection, fetchSavedSelections, renameSavedSelection, saveSelection } from "../api";
import type { BetCodePick, SavedSelection } from "../types";
import { ErrorState } from "./ErrorState";

/**
 * Saved selections for AI Generation: keep a set of picks now, turn it into a
 * booking code later. Only the picks are stored. "Use" puts them back through
 * the normal check (today's predictions, games that have started dropped), then
 * the usual betting-site buttons make the code.
 */

function savedDate(iso: string): string {
  return new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "Save for later" next to a generated slip: a button that opens a small
 * name box. Naming is optional. */
export function SaveSelectionControl({ picks, onSaved }: { picks: BetCodePick[]; onSaved: () => void }) {
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const saved = await saveSelection(picks, name);
      setAsking(false);
      setName("");
      setMessage({
        text:
          `Saved as “${saved.name}”. Find it under Saved selections below.` +
          (saved.warnings.length > 0 ? ` ${saved.warnings.join(" ")}` : ""),
        error: false,
      });
      onSaved();
    } catch (err) {
      setMessage({ text: String(err instanceof Error ? err.message : err), error: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {!asking && (
        <button type="button" className="btn ghost" onClick={() => { setAsking(true); setMessage(null); }}>
          Save for later
        </button>
      )}
      {asking && (
        <form onSubmit={handleSave} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            aria-label="Name for this saved selection"
            placeholder="Name (optional)"
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
            style={{ minWidth: 190 }}
          />
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button className="btn ghost" type="button" onClick={() => setAsking(false)} disabled={busy}>
            Cancel
          </button>
        </form>
      )}
      {message && (
        <p
          className={message.error ? "auth-error" : "setting-note"}
          role="status"
          style={{ flexBasis: "100%", margin: 0 }}
        >
          {message.text}
        </p>
      )}
    </>
  );
}

export function SavedSelections({
  reloadKey,
  onUse,
}: {
  /** Bump to reload the list (after a new one is saved). */
  reloadKey: number;
  onUse: (picks: BetCodePick[], name: string) => void;
}) {
  const [items, setItems] = useState<SavedSelection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSavedSelections()
      .then((rows) => !cancelled && (setItems(rows), setError(null)))
      .catch((e) => !cancelled && setError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function handleDelete(item: SavedSelection) {
    if (!window.confirm(`Delete “${item.name}”? This can't be undone.`)) return;
    setBusyId(item.id);
    try {
      await deleteSavedSelection(item.id);
      setItems((prev) => prev?.filter((s) => s.id !== item.id) ?? prev);
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleRename(item: SavedSelection) {
    const next = window.prompt("New name for this saved selection:", item.name);
    if (next === null || !next.trim() || next.trim() === item.name) return;
    setBusyId(item.id);
    try {
      const updated = await renameSavedSelection(item.id, next.trim());
      setItems((prev) => prev?.map((s) => (s.id === item.id ? updated : s)) ?? prev);
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="card card-pad" style={{ marginTop: 20 }}>
      <div className="section-header" style={{ marginBottom: 6 }}>
        <h3 style={{ margin: 0 }}>Saved selections</h3>
        <span className="meta">Keep a slip now, get its booking code later</span>
      </div>

      {error && <ErrorState message={error} />}
      {!error && items === null && <p className="sub">Loading…</p>}
      {items !== null && items.length === 0 && (
        <p className="setting-note" style={{ margin: 0 }}>
          Nothing saved yet. After you generate a slip, press “Save for later” and it will wait for you here.
        </p>
      )}

      {items?.map((item) => {
        const total = item.games.length;
        const allGone = item.upcoming_count === 0;
        const someGone = !allGone && item.upcoming_count < total;
        return (
          <div key={item.id} style={{ padding: "10px 0", borderTop: "1px solid var(--border)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ flex: "1 1 220px", minWidth: 0 }}>
                <strong>{item.name}</strong>{" "}
                <span className="sub">
                  {total} game{total === 1 ? "" : "s"} · saved {savedDate(item.created_at)}
                </span>
                {allGone && (
                  <div className="meta" style={{ marginTop: 2 }}>
                    All of these games have started or finished, so this can't be used any more.
                  </div>
                )}
                {someGone && (
                  <div className="meta" style={{ marginTop: 2, color: "var(--warning)" }}>
                    {item.upcoming_count} of {total} games are still to play. Only those will be kept when you use it.
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="btn"
                  style={{ padding: "4px 12px", fontSize: 13 }}
                  disabled={allGone || busyId === item.id}
                  onClick={() =>
                    onUse(
                      item.games.map((g) => ({ match_id: g.match_id, market: g.market, selection: g.selection })),
                      item.name,
                    )
                  }
                >
                  Use
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  style={{ padding: "4px 10px", fontSize: 13 }}
                  onClick={() => setOpenId(openId === item.id ? null : item.id)}
                  aria-expanded={openId === item.id}
                >
                  {openId === item.id ? "Hide games" : "Show games"}
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  style={{ padding: "4px 10px", fontSize: 13 }}
                  disabled={busyId === item.id}
                  onClick={() => handleRename(item)}
                >
                  Rename
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  style={{ padding: "4px 10px", fontSize: 13 }}
                  disabled={busyId === item.id}
                  onClick={() => handleDelete(item)}
                >
                  Delete
                </button>
              </div>
            </div>
            {openId === item.id && (
              <ul className="doc-list" style={{ marginTop: 8 }}>
                {item.games.map((g) => (
                  <li key={`${g.match_id}|${g.market}|${g.selection}`} style={{ opacity: g.upcoming ? 1 : 0.55 }}>
                    <strong>
                      {g.home_team}
                      {g.away_team ? ` vs ${g.away_team}` : ""}
                    </strong>{" "}
                    -- {g.market}: {g.selection}
                    {g.kickoff && (
                      <span className="sub">
                        {" "}
                        · {new Date(g.kickoff.endsWith("Z") ? g.kickoff : `${g.kickoff}Z`).toLocaleString(undefined, {
                          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                        })}
                      </span>
                    )}
                    {!g.upcoming && <span className="sub"> · started or finished</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
