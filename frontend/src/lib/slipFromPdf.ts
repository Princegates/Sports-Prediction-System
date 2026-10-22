/**
 * Turns a SportyBet ticket saved as a PDF (the browser's "Print -> Save as
 * PDF" of a ticket page) into one upload-ready image, entirely in the
 * browser -- the PDF itself never leaves the admin's device.
 *
 * Per page: render it, keep only the ticket panel (the dark block -- drops
 * the white page margins and the print header/footer, which carry the
 * account's bet-history URL), and trim the empty space under the last leg.
 * Everything from "Number of Bets" down is cut off: that block holds the
 * transaction-history link, "Delete Ticket" and the IP address the bet was
 * placed from. Any IP-looking text left anywhere else is painted over too.
 * The pages are then stacked into one tall JPEG.
 *
 * The ticket text is also read for a suggested caption ("13 legs · GH₵100
 * → GH₵1,081.15") and win date (the last leg's kickoff day).
 *
 * pdf.js is loaded on demand, so it only ever downloads for an admin who
 * actually picks a PDF.
 */

const RENDER_SCALE = 2;
const MAX_WIDTH = 1080;
const MAX_HEIGHT = 6000;
const IP_PATTERN = /\b\d{1,3}(?:\.\d{1,3}){3}\b/;

export interface SlipConversion {
  file: File;
  pages: number;
  caption: string;
  wonOn: string | null;
  won: boolean;
  /** Things the admin should double-check before posting. */
  warnings: string[];
}

interface TextBox {
  str: string;
  left: number;
  top: number;
  width: number;
  height: number;
}

interface PagePart {
  canvas: HTMLCanvasElement;
  left: number;
  top: number;
  right: number;
  bottom: number;
}

async function loadPdfJs() {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const worker = await import("pdfjs-dist/legacy/build/pdf.worker.min.mjs?url");
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
  return pdfjs;
}

/** True for a pixel that isn't (near-)white page background. */
function isInk(d: Uint8ClampedArray, i: number): boolean {
  return d[i] < 235 || d[i + 1] < 235 || d[i + 2] < 235;
}

/** The ticket panel's bounds on a rendered page: the rows and columns that
 * are mostly non-white, above `cutAt` -- sparse print-footer text never
 * qualifies, the solid ticket panel always does. */
function panelBounds(ctx: CanvasRenderingContext2D, w: number, h: number, cutAt: number) {
  const d = ctx.getImageData(0, 0, w, h).data;
  const limit = Math.min(h, Math.max(0, Math.floor(cutAt)));
  let top = -1;
  let bottom = -1;
  for (let y = 0; y < limit; y++) {
    let ink = 0;
    for (let x = 0; x < w; x += 2) if (isInk(d, (y * w + x) * 4)) ink++;
    if (ink / (w / 2) > 0.6) {
      if (top < 0) top = y;
      bottom = y;
    }
  }
  if (top < 0) return null;

  let left = -1;
  let right = -1;
  const rows = bottom - top + 1;
  for (let x = 0; x < w; x++) {
    let ink = 0;
    for (let y = top; y <= bottom; y += 4) if (isInk(d, (y * w + x) * 4)) ink++;
    if (ink / (rows / 4) > 0.6) {
      if (left < 0) left = x;
      right = x;
    }
  }
  if (left < 0) return null;

  // Drop trailing rows that are one flat colour (the empty panel under the
  // last leg), keeping a little breathing room.
  const flat = (y: number) => {
    const base = (y * w + left) * 4;
    for (let x = left; x <= right; x += 3) {
      const i = (y * w + x) * 4;
      if (Math.abs(d[i] - d[base]) + Math.abs(d[i + 1] - d[base + 1]) + Math.abs(d[i + 2] - d[base + 2]) > 24) return false;
    }
    return true;
  };
  let contentBottom = bottom;
  while (contentBottom > top && flat(contentBottom)) contentBottom--;
  bottom = Math.min(bottom, contentBottom + 24);

  return { left: left + 1, top: top + 1, right: right - 1, bottom };
}

/** The amount printed on the same line as a label, to its right. A printed
 * ticket's text runs all the labels first and all the amounts after, so
 * reading order alone can't pair them -- position can. */
function amountBeside(boxes: TextBox[], label: RegExp): string | null {
  const key = boxes.find((b) => label.test(b.str.trim()));
  if (!key) return null;
  const mid = key.top + key.height / 2;
  const candidates = boxes.filter(
    (b) => /^[\d,]+(?:\.\d{2})?$/.test(b.str.trim()) && b.left > key.left && Math.abs(b.top + b.height / 2 - mid) < key.height * 0.8,
  );
  candidates.sort((a, b) => a.left - b.left);
  // "100.00" reads better as "100" in a caption; "1,081.15" stays as is.
  return candidates[0]?.str.trim().replace(/\.00$/, "") ?? null;
}

function parseTicket(text: string, boxes: TextBox[], warnings: string[]) {
  const stake = amountBeside(boxes, /^Total Stake$/i);
  const ret = amountBeside(boxes, /^Total SportyBet Return$/i);
  const legs = (text.match(/Game ID:/gi) ?? []).length;
  const won = /\bWon\b/.test(text);
  const currency = /sportybet\.com\/gh\b|GH[S₵]/i.test(text) ? "GH₵" : "";

  const parts: string[] = [];
  if (legs) parts.push(`${legs} leg${legs === 1 ? "" : "s"}`);
  if (stake && ret) parts.push(`${currency}${stake} → ${currency}${ret}`);
  const caption = parts.join(" · ");

  // The win date is the last leg's kickoff day ("Game ID: 123 | 17/10 18:00").
  let wonOn: string | null = null;
  const days = [...text.matchAll(/Game ID:\s*\d+\s*\|\s*(\d{2})\/(\d{2})/gi)].map((m) => [Number(m[1]), Number(m[2])]);
  if (days.length > 0) {
    const today = new Date();
    const dated = days.map(([day, month]) => {
      let d = new Date(today.getFullYear(), month - 1, day);
      if (d.getTime() > today.getTime() + 86_400_000) d = new Date(today.getFullYear() - 1, month - 1, day);
      return d;
    });
    const last = dated.reduce((a, b) => (b > a ? b : a));
    wonOn = `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, "0")}-${String(last.getDate()).padStart(2, "0")}`;
  }

  if (!won) warnings.push("This ticket doesn't say “Won” anywhere -- check it's the right one.");
  if (!legs) warnings.push("Couldn't read the legs from this PDF -- check the image before posting.");
  return { caption, wonOn, won };
}

export async function slipFromPdf(file: File): Promise<SlipConversion> {
  const pdfjs = await loadPdfJs();
  // No eval: the site's Content-Security-Policy forbids it, and pdf.js
  // renders the same without it.
  const pdf = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()), isEvalSupported: false }).promise;
  const warnings: string[] = [];
  const parts: PagePart[] = [];
  const allText: string[] = [];
  const firstPageBoxes: TextBox[] = [];
  let foundCut = false;
  let redactedIps = 0;

  for (let n = 1; n <= pdf.numPages; n++) {
    const page = await pdf.getPage(n);
    const viewport = page.getViewport({ scale: RENDER_SCALE });
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("This browser can't process images.");
    await page.render({ canvasContext: ctx, viewport }).promise;

    const content = await page.getTextContent();
    const boxes: TextBox[] = [];
    for (const item of content.items) {
      if (!("str" in item) || !item.str.trim()) continue;
      const [x, y] = viewport.convertToViewportPoint(item.transform[4], item.transform[5]);
      const height = Math.hypot(item.transform[2], item.transform[3]) * RENDER_SCALE;
      boxes.push({ str: item.str, left: x, top: y - height, width: item.width * RENDER_SCALE, height });
    }
    allText.push(boxes.map((b) => b.str).join(" "));
    if (n === 1) firstPageBoxes.push(...boxes);

    // Everything from "Number of Bets" down goes (see this module's docstring).
    let cutAt = canvas.height;
    const marker = boxes.find((b) => /Number of Bets/i.test(b.str));
    if (marker) {
      cutAt = marker.top - 12 * RENDER_SCALE;
      foundCut = true;
    }

    // Paint over any IP address that's still above the cut.
    for (const b of boxes) {
      if (!IP_PATTERN.test(b.str) || b.top > cutAt) continue;
      const pad = 4 * RENDER_SCALE;
      const sample = ctx.getImageData(Math.max(0, Math.floor(b.left - pad * 2)), Math.floor(b.top + b.height / 2), 1, 1).data;
      ctx.fillStyle = `rgb(${sample[0]}, ${sample[1]}, ${sample[2]})`;
      ctx.fillRect(b.left - pad, b.top - pad, b.width + pad * 2, b.height + pad * 2);
      redactedIps++;
    }

    const bounds = panelBounds(ctx, canvas.width, canvas.height, cutAt);
    if (bounds && bounds.bottom > bounds.top) parts.push({ canvas, ...bounds });
  }

  if (parts.length === 0) throw new Error("Couldn't find a SportyBet ticket in this PDF.");
  if (!foundCut) {
    warnings.push(
      "Couldn't find the “Number of Bets” section to cut off -- check the bottom of the image for an IP address or account details before posting.",
    );
  }

  // Stack the pages at one common width.
  const width = Math.max(...parts.map((p) => p.right - p.left));
  const heights = parts.map((p) => Math.round(((p.bottom - p.top) * width) / (p.right - p.left)));
  const total = heights.reduce((a, b) => a + b, 0);
  const scale = Math.min(1, MAX_WIDTH / width, MAX_HEIGHT / total);
  const out = document.createElement("canvas");
  out.width = Math.round(width * scale);
  out.height = Math.round(total * scale);
  const octx = out.getContext("2d");
  if (!octx) throw new Error("This browser can't process images.");
  let y = 0;
  parts.forEach((p, i) => {
    const h = Math.round(heights[i] * scale);
    octx.drawImage(p.canvas, p.left, p.top, p.right - p.left, p.bottom - p.top, 0, y, out.width, h);
    y += h;
  });

  const blob = await new Promise<Blob>((resolve, reject) =>
    out.toBlob((b) => (b ? resolve(b) : reject(new Error("Couldn't create the image."))), "image/jpeg", 0.88),
  );
  const { caption, wonOn, won } = parseTicket(allText.join("\n"), firstPageBoxes, warnings);
  if (redactedIps > 0) warnings.push(`Covered ${redactedIps} IP address${redactedIps === 1 ? "" : "es"} elsewhere on the ticket.`);

  return {
    file: new File([blob], file.name.replace(/\.pdf$/i, "") + ".jpg", { type: "image/jpeg" }),
    pages: pdf.numPages,
    caption,
    wonOn,
    won,
    warnings,
  };
}
