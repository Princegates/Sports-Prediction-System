import { useEffect, useState } from "react";
import type { WinningSlipUpload } from "../api";
import { readAsDataUrl, resizeImage } from "../lib/imageResize";
import type { SlipConversion } from "../lib/slipFromPdf";

/**
 * The winning-slip upload form, shared by Admin -> Winning slips and a
 * member's Share a win page. Takes a screenshot or a SportyBet ticket saved
 * as a PDF; a PDF is converted to one image in the browser first
 * (lib/slipFromPdf: ticket panel only, account link and IP address cut off),
 * with the caption and win date filled in from the ticket text.
 */
export function WinningSlipForm({
  onSubmit,
  submitLabel,
  busyLabel = "Posting…",
}: {
  onSubmit: (body: WinningSlipUpload) => Promise<unknown>;
  submitLabel: string;
  busyLabel?: string;
}) {
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
      // Suggestions only -- never overwrite what was already typed.
      setCaption((c) => c || result.caption);
      setWonOn((d) => d || result.wonOn || "");
    } catch (err) {
      setFormError(`Couldn't convert that PDF -- ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setConverting(false);
    }
  }

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
    const form = e.target as HTMLFormElement;
    try {
      const { dataUrl, width, height } = await resizeImage(file);
      await onSubmit({ image_data: dataUrl, caption: caption.trim(), won_on: wonOn || null, width, height });
      setFile(null);
      setConversion(null);
      setCaption("");
      setWonOn("");
      form.reset();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
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
          Kept the ticket only -- page margins, the print footer (your account link), and everything from “Number of
          Bets” down (transaction history, Delete Ticket, IP address) were removed. Check the preview before posting.
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
        automatically. For a screenshot, crop out or cover anything private first: account balance, phone number, user
        ID. Either way the image is resized and its photo metadata removed before upload.
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
          {busy ? busyLabel : submitLabel}
        </button>
      </div>
    </form>
  );
}
