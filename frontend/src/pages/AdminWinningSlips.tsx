import { useEffect, useState } from "react";
import { apiUrl, deleteWinningSlip, fetchAdminWinningSlips, postWinningSlip, updateWinningSlip } from "../api";
import { ErrorState } from "../components/ErrorState";
import { readAsDataUrl, resizeImage } from "../lib/imageResize";
import type { SlipConversion } from "../lib/slipFromPdf";
import type { WinningSlip } from "../types";

/**
 * Admin -> Winning slips: post screenshots of won SportyBet slips. Each one
 * shows on the public home page and the member Dashboard straight away
 * (WinningSlipsGallery); hide or delete it here.
 *
 * A ticket saved as a PDF is converted to one image in the browser first
 * (lib/slipFromPdf: ticket panel only, account link and IP address cut
 * off), and its caption and win date filled in from the ticket text.
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
  const [converting, setConverting] = useState(false);
  const [conversion, setConversion] = useState<SlipConversion | null>(null);

  async function pickFile(picked: File | null) {
    setFormError(null);
    setConversion(null);
    setFile(null);
    if (!picked) return;
    const isPdf = picked.type === "application/pdf" || /\.pdf$/i.test(picked.name);
    if (!isPdf) {
      setFile(picked);
      return;
    }
    setConverting(true);
    try {
      const { slipFromPdf } = await import("../lib/slipFromPdf");
      const result = await slipFromPdf(picked);
      setConversion(result);
      setFile(result.file);
      // Suggestions only -- never overwrite what the admin already typed.
      setCaption((c) => c || result.caption);
      setWonOn((d) => d || result.wonOn || "");
    } catch (err) {
      setFormError(`Couldn't convert that PDF -- ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setConverting(false);
    }
  }

  function load() {
    setError(null);
    fetchAdminWinningSlips()
      .then(setSlips)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }

  useEffect(load, []);

  // A data: URL, not a blob: one -- see readAsDataUrl for why.
  useEffect(() => {
    setPreview(null);
    if (!file) return;
    let cancelled = false;
    readAsDataUrl(file)
      .then((url) => !cancelled && setPreview(url))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
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
      setConversion(null);
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
          <span>Screenshot or ticket PDF</span>
          <input
            type="file"
            accept="image/*,application/pdf,.pdf"
            required
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </label>
        {converting && (
          <p className="meta" role="status" style={{ margin: 0 }}>
            Converting the PDF to an image…
          </p>
        )}
        {conversion && (
          <div className="wins-admin-conversion">
            <strong>
              Converted {conversion.pages} page{conversion.pages === 1 ? "" : "s"} into one image.
            </strong>{" "}
            Kept the ticket only -- page margins, the print footer (your account link), and everything from “Number
            of Bets” down (transaction history, Delete Ticket, IP address) were removed. Check the preview before
            posting.
            {conversion.warnings.map((w) => (
              <p key={w} className="wins-admin-warning">
                {w}
              </p>
            ))}
          </div>
        )}
        {preview && (
          <div className="wins-admin-preview-box">
            <img className="wins-admin-preview" src={preview} alt="Preview" />
          </div>
        )}
        <p className="setting-note" style={{ margin: 0 }}>
          A screenshot, or the ticket saved as a PDF from SportyBet (Print → Save as PDF) -- a PDF is converted
          automatically. For a screenshot, crop out or cover anything private first: account balance, phone number,
          user ID. Either way the image is resized and its photo metadata removed before upload.
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
          <button className="btn" type="submit" disabled={!file || busy || converting}>
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
