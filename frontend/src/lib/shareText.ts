/** Turns a plain pick description into a shareable message that also
 * carries the sharer's own referral link when they have one -- a correct
 * call is the best advertising this product has, and this is what turns
 * sharing one into a new signup instead of just a screenshot. Falls back to
 * a plain site link for the rare account without a code yet (see
 * app.access.ensure_referral_code -- every real account gets one, so this
 * is mostly a defensive fallback). */
export function buildShareMessage(pickText: string, referralCode?: string | null): string {
  const origin = window.location.origin;
  const cta = referralCode
    ? `Try Socca Intelligence -- sign up with my code ${referralCode} and we both get bonus days: ${origin}/register?ref=${referralCode}`
    : `Try Socca Intelligence: ${origin}`;
  return `${pickText}\n\n${cta}`;
}
