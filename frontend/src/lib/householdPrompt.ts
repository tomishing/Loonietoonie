/** "Not now" on the Patterns household banner, remembered on this device only. */
const DISMISSED_KEY = 'loonietoonie.householdPromptDismissed'

export function householdPromptDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISSED_KEY) === '1'
  } catch {
    return false
  }
}

export function rememberHouseholdPromptDismissed(): void {
  try {
    localStorage.setItem(DISMISSED_KEY, '1')
  } catch {
    // Private mode or blocked storage: the banner just comes back next time.
  }
}
