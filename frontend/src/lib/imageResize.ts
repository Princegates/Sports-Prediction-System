/**
 * Shrinks an image file in the browser before upload: longest side at most
 * `maxSide` px, re-encoded as JPEG. Keeps uploads small (a phone screenshot
 * drops from several MB to a few hundred KB) and, because it redraws the
 * pixels onto a canvas, drops any photo metadata the file carried.
 */
export async function resizeImage(
  file: File,
  maxSide = 1280,
  quality = 0.85,
): Promise<{ dataUrl: string; width: number; height: number }> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("That file isn't an image this browser can read."));
      el.src = url;
    });
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
    const width = Math.max(1, Math.round(img.naturalWidth * scale));
    const height = Math.max(1, Math.round(img.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("This browser can't process images.");
    // JPEG has no transparency -- paint a white background first.
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);
    return { dataUrl: canvas.toDataURL("image/jpeg", quality), width, height };
  } finally {
    URL.revokeObjectURL(url);
  }
}
