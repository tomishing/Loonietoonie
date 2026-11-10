/** The public privacy policy (GitHub Pages, `docs/privacy.html`); also on the Google OAuth consent screen. */
export const PRIVACY_POLICY_URL = 'https://tomishing.github.io/Loonietoonie/privacy.html'
/** How to delete your data (`docs/delete.html`); also the Delete account URL in Play's Data safety form. */
export const DELETE_DATA_URL = 'https://tomishing.github.io/Loonietoonie/delete.html'
/** Where feedback and sign-in problems go (the developer address on the Play listing and delete page). */
export const FEEDBACK_EMAIL = 'loonietoonietracker.dev@gmail.com'

/** A mailto: link to FEEDBACK_EMAIL; opens the mail app on Android too. */
export function feedbackUrl(subject: string): string {
  return `mailto:${FEEDBACK_EMAIL}?subject=${encodeURIComponent(subject)}`
}
