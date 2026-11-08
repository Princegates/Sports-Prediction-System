/**
 * Facts the Terms, Privacy and Refund pages share, in one place so a change
 * (a new date, a support email) is made once.
 *
 * Optional fields are left out of the pages when empty rather than showing a
 * blank or a placeholder to the public.
 */
export const LEGAL = {
  /** The trading name of the service. */
  site: "Soccaintel.com (Socca Intelligence)",
  /** Who runs it -- as the footer already says: "Powered by Anknovate IT Services". */
  operator: "Anknovate IT Services",
  /** When the three documents were last changed. Update this with any wording change. */
  updated: "8 November 2026",
  /** Public support email, if there is one. Leave empty to show WhatsApp only. */
  contactEmail: "",
  /** Governing law and the data-protection authority that goes with it. */
  country: "Ghana",
  /** How long a refund request can be made after a payment. */
  refundWindowDays: 14,
};
