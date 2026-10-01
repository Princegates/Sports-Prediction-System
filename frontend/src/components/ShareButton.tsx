import { whatsappLink } from "../lib/whatsapp";

interface Props {
  text: string;
  label?: string;
  className?: string;
}

/** The native share sheet when the browser offers one (covers WhatsApp,
 * SMS, Telegram, whatever the OS lists) -- otherwise falls back to a
 * WhatsApp web link with no recipient set, which lets the user pick who to
 * send it to. WhatsApp specifically because that's where this audience
 * already is (see lib/whatsapp.ts's own "contact admin" use). */
export function ShareButton({ text, label = "Share", className = "btn ghost" }: Props) {
  async function handleShare() {
    if (navigator.share) {
      try {
        await navigator.share({ text });
        return;
      } catch {
        // Cancelled, or unsupported despite existing -- either way, the
        // WhatsApp fallback below still gets the message out.
      }
    }
    window.open(whatsappLink("", text), "_blank", "noopener,noreferrer");
  }

  return (
    <button type="button" className={className} onClick={handleShare}>
      {label}
    </button>
  );
}
