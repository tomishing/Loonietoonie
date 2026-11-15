/** The public site: home page, privacy policy, and Delete your data (GitHub Pages from `docs/`). */
export const SITE_URL = 'https://loonietoonietracker.com'
/** The public privacy policy (GitHub Pages from `docs/`, custom domain in `docs/CNAME`); also on the Google OAuth consent screen. */
export const PRIVACY_POLICY_URL = `${SITE_URL}/privacy.html`
/** How to delete your data (`docs/delete.html`); also the Delete account URL in Play's Data safety form. */
export const DELETE_DATA_URL = `${SITE_URL}/delete.html`
/** Where feedback and sign-in problems go (the developer address on the Play listing and delete page). */
export const FEEDBACK_EMAIL = 'loonietoonietracker.dev@gmail.com'

/** A mailto: link to FEEDBACK_EMAIL; opens the mail app on Android too. */
export function feedbackUrl(subject: string): string {
  return `mailto:${FEEDBACK_EMAIL}?subject=${encodeURIComponent(subject)}`
}
