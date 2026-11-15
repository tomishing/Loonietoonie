import { describe, expect, it } from 'vitest'
import cname from '../../../docs/CNAME?raw'
import deletePage from '../../../docs/delete.html?raw'
import privacyPage from '../../../docs/privacy.html?raw'
import { DELETE_DATA_URL, FEEDBACK_EMAIL, PRIVACY_POLICY_URL, SITE_URL, feedbackUrl } from './links'

describe('feedbackUrl', () => {
  it('builds a mailto link with an encoded subject', () => {
    expect(feedbackUrl('LoonieToonie sign-in problem (Android)')).toBe(
      `mailto:${FEEDBACK_EMAIL}?subject=LoonieToonie%20sign-in%20problem%20(Android)`,
    )
  })
})

describe('public page links', () => {
  it('uses the GitHub Pages custom domain from docs/CNAME over HTTPS', () => {
    expect(new URL(SITE_URL).protocol).toBe('https:')
    expect(new URL(SITE_URL).host).toBe(cname.trim())
  })

  it('points at pages that exist in docs/', () => {
    expect(PRIVACY_POLICY_URL).toBe(`${SITE_URL}/privacy.html`)
    expect(DELETE_DATA_URL).toBe(`${SITE_URL}/delete.html`)
    expect(privacyPage).toContain('<title>LoonieToonie: Expense Tracker Privacy Policy</title>')
    expect(deletePage).toContain('<title>Delete your LoonieToonie: Expense Tracker data</title>')
  })

  it('no longer links to the old github.io address', () => {
    for (const url of [SITE_URL, PRIVACY_POLICY_URL, DELETE_DATA_URL]) {
      expect(url).not.toContain('github.io')
    }
  })
})
