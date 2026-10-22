import { useEffect, useState } from "react";
import { apiUrl, deleteWinningSlip, fetchAdminWinningSlips, postWinningSlip, updateWinningSlip } from "../api";
import { ErrorState } from "../components/ErrorState";
import { resizeImage } from "../lib/imageResize";
import type { WinningSlip } from "../types";

/**
 * Admin -> Winning slips: post screenshots of won SportyBet slips. Each one
 * shows on the public home page and the member Dashboard straight away
 * (WinningSlipsGallery); hide or delete it here.
 */
export function AdminWinningSlips() {
  const [slips, setSlips] = useState<WinningSlip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [caption, setCaption] = useState("");
  const [wonOn, setWonOn] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function load() {
    setError(null);
    fetchAdminWinningSlips()
      .then(setSlips)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }

  useEffect(load, []);

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  async function handlePost(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setFormError(null);
    try {
      const { dataUrl, width, height } = await resizeImage(file);
      await postWinningSlip({ image_data: dataUrl, caption: caption.trim(), won_on: wonOn || null, width, height });
      setFile(null);
      setCaption("");
      setWonOn("");
      (e.target as HTMLFormElement).reset();
      load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
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

  if (error && slips === null) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="admin-header">
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>Winning slips</h1>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0" }}>
            Screenshots of won SportyBet slips. They show on the public home page and every member's Dashboard as
            soon as you post them.
          </p>
        </div>
      </div>

      <form className="card card-pad wins-admin-form" onSubmit={handlePost}>
        <label className="setting-field">
          <span>Screenshot</span>
          <input type="file" accept="image/*" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        {preview && <img className="wins-admin-preview" src={preview} alt="Preview" />}
        <p className="setting-note" style={{ margin: 0 }}>
          Crop out or cover anything private first -- account balance, phone number, user ID. The image is shrunk
          and its photo metadata removed before upload.
        </p>
        <label className="setting-field">
          <span>Caption (optional)</span>
          <input
            type="text"
            maxLength={200}
            value={caption}
            placeholder="e.g. 5-leg slip, GH₵20 → GH₵310"
            onChange={(e) => setCaption(e.target.value)}
          />
        </label>
        <label className="setting-field">
          <span>Won on (optional)</span>
          <input type="date" value={wonOn} onChange={(e) => setWonOn(e.target.value)} />
        </label>
        {formError && <p className="auth-error">{formError}</p>}
        <div>
          <button className="btn" type="submit" disabled={!file || busy}>
            {busy ? "Posting…" : "Post winning slip"}
          </button>
        </div>
      </form>

      {error && <p className="auth-error" style={{ marginTop: 12 }}>{error}</p>}

      <div className="wins-admin-list">
        {slips?.length === 0 && <p className="meta">Nothing posted yet.</p>}
        {slips?.map((s) => (
          <div key={s.id} className={`card wins-admin-item${s.published ? "" : " hidden-slip"}`}>
            {/* The image is only served while published -- a hidden slip is
                hidden from everyone, this list included. */}
            {s.published ? (
              <img src={apiUrl(s.image_path)} alt={s.caption || "Winning slip"} loading="lazy" />
            ) : (
              <div className="wins-admin-hidden">Hidden</div>
            )}
            <div className="wins-admin-meta">
              <strong>{s.caption || "No caption"}</strong>
              <span className="meta">
                {s.won_on ? `Won ${s.won_on} · ` : ""}posted {new Date(s.created_at).toLocaleDateString()}
                {!s.published && " · hidden"}
              </span>
              <div className="admin-actions">
                <button className="btn ghost btn-sm" onClick={() => act(() => updateWinningSlip(s.id, { published: !s.published }))}>
                  {s.published ? "Hide" : "Show"}
                </button>
                <button
                  className="btn ghost btn-sm admin-delete-btn"
                  onClick={() => window.confirm("Delete this winning slip for good?") && act(() => deleteWinningSlip(s.id))}
                >
                  Delete
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
