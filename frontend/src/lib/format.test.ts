import { describe, expect, it } from 'vitest'
import { formatMoney, formatMonth, monthRange } from './format'

describe('monthRange', () => {
  it('covers the whole month', () => {
    expect(monthRange('2026-10')).toEqual({ from: '2026-10-01', to: '2026-10-31' })
    expect(monthRange('2026-02')).toEqual({ from: '2026-02-01', to: '2026-02-28' })
    expect(monthRange('2028-02')).toEqual({ from: '2028-02-01', to: '2028-02-29' })
  })

  it('rejects other input', () => {
    expect(monthRange('all')).toBeNull()
  })
})

describe('formatMonth', () => {
  it('names the month and year', () => {
    expect(formatMonth('2026-06')).toMatch(/2026/)
    expect(formatMonth('bad')).toBe('bad')
  })
})

describe('formatMoney', () => {
  it('falls back for unknown currency codes', () => {
    expect(formatMoney('5', 'ZZZZ')).toBe('5 ZZZZ')
  })

  it('formats decimal strings', () => {
    expect(formatMoney('1234.5', 'USD')).toMatch(/1,?234\.50/)
  })
})
