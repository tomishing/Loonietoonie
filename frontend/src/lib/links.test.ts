import { describe, expect, it } from 'vitest'
import { FEEDBACK_EMAIL, feedbackUrl } from './links'

describe('feedbackUrl', () => {
  it('builds a mailto link with an encoded subject', () => {
    expect(feedbackUrl('LoonieToonie sign-in problem (Android)')).toBe(
      `mailto:${FEEDBACK_EMAIL}?subject=LoonieToonie%20sign-in%20problem%20(Android)`,
    )
  })
})
