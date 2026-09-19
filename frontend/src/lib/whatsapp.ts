/** Mirrors app/mailer.py's format_whatsapp -- both take the digits-only
 * contact_whatsapp setting (e.g. "233596909643") and format it for display. */
export function formatWhatsapp(digits: string): string {
  if (!digits) return "";
  if (digits.length === 12) {
    return `+${digits.slice(0, 3)} ${digits.slice(3, 5)} ${digits.slice(5, 8)} ${digits.slice(8)}`;
  }
  return `+${digits}`;
}

export function whatsappLink(digits: string, message?: string): string {
  const base = `https://wa.me/${digits}`;
  return message ? `${base}?text=${encodeURIComponent(message)}` : base;
}
