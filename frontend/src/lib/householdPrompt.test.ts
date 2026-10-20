import { afterEach, describe, expect, it, vi } from 'vitest'
import { householdPromptDismissed, rememberHouseholdPromptDismissed } from './householdPrompt'

function memoryStorage(): Storage {
  const values = new Map<string, string>()
  return {
    get length() {
      return values.size
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, value),
  }
}

describe('household prompt dismissal', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('is remembered after "Not now"', () => {
    vi.stubGlobal('localStorage', memoryStorage())
    expect(householdPromptDismissed()).toBe(false)
    rememberHouseholdPromptDismissed()
    expect(householdPromptDismissed()).toBe(true)
  })

  it('shows the prompt when storage is blocked', () => {
    const blocked = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    }
    vi.stubGlobal('localStorage', blocked)
    expect(() => rememberHouseholdPromptDismissed()).not.toThrow()
    expect(householdPromptDismissed()).toBe(false)
  })
})
