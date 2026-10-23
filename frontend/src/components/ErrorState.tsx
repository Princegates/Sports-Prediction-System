import { useEffect, useState } from "react";
import { fetchBranding } from "../api";
import { whatsappLink } from "../lib/whatsapp";

interface Props {
  message: string;
  onRetry?: () => void;
}

/** Errors that tell the user to "Contact a Super Admin" (e.g. the daily AI
 * Generation quota) get a one-tap WhatsApp button to the admin. */
const NEEDS_ADMIN = /contact a super admin/i;

export function ErrorState({ message, onRetry }: Props) {
  const [whatsapp, setWhatsapp] = useState<string | null>(null);
  const needsAdmin = NEEDS_ADMIN.test(message);

  useEffect(() => {
    if (!needsAdmin) return;
    fetchBranding()
      .then((b) => setWhatsapp(b.contact_whatsapp || null))
      .catch(() => {});
  }, [needsAdmin]);

  return (
    <div className="state-card">
      <div className="icon">⚠</div>
      <div>Some match data is temporarily unavailable.</div>
      <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-muted)" }}>{message}</div>
      {needsAdmin && whatsapp && (
        <a
          className="btn"
          style={{ marginTop: 16, display: "inline-block" }}
          href={whatsappLink(whatsapp, "Hi, I've reached my daily AI Generation limit on Socca Intelligence and need a higher limit.")}
          target="_blank"
          rel="noreferrer noopener"
        >
          Contact admin on WhatsApp
        </a>
      )}
      {onRetry && (
        <button className="btn ghost" style={{ marginTop: 16 }} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
