import { useEffect, useState } from "react";
import { fetchBranding } from "../api";
import { LEGAL } from "../lib/legal";
import { whatsappLink } from "../lib/whatsapp";

/** The "Questions?" block at the end of every legal page: WhatsApp (read from
 * the site's settings) and the support email if one is set. */
export function LegalContact({ topic }: { topic: string }) {
  const [whatsapp, setWhatsapp] = useState<string | null>(null);

  useEffect(() => {
    fetchBranding()
      .then((b) => setWhatsapp(b.contact_whatsapp || null))
      .catch(() => setWhatsapp(null));
  }, []);

  return (
    <section className="doc-cta">
      <h2>Questions?</h2>
      <p>
        Contact {LEGAL.operator} and we'll answer directly.
        {LEGAL.contactEmail && (
          <>
            {" "}
            Email: <a href={`mailto:${LEGAL.contactEmail}`}>{LEGAL.contactEmail}</a>.
          </>
        )}
      </p>
      {whatsapp && (
        <div className="landing-cta-row">
          <a
            className="btn btn-lg"
            href={whatsappLink(whatsapp, `Hi, I have a question about your ${topic}.`)}
            target="_blank"
            rel="noreferrer noopener"
          >
            Message us on WhatsApp
          </a>
        </div>
      )}
    </section>
  );
}
