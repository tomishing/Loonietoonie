import { describe, expect, it } from 'vitest'
import { formatDecimal, lineTotal, multiply, parseDecimal, sum } from './decimal'

const d = (text: string) => {
  const value = parseDecimal(text)
  if (value === null) throw new Error(`bad test value ${text}`)
  return value
}

describe('parseDecimal', () => {
  it.each([
    ['12', '12'],
    ['-1.5', '-1.5'],
    ['.25', '0.25'],
    ['3.490', '3.49'],
    [' 7 ', '7'],
  ])('parses %j', (text, expected) => {
    expect(formatDecimal(d(text))).toBe(expected)
  })

  it.each(['', '-', '.', 'abc', '1.2345', '1,000', '1e3', 'NaN'])('rejects %j', (text) => {
    expect(parseDecimal(text)).toBeNull()
  })
})

describe('money math without floats', () => {
  it('adds exactly', () => {
    expect(formatDecimal(sum([d('0.1'), d('0.2')]))).toBe('0.3')
  })

  it('multiplies quantity by unit price', () => {
    expect(formatDecimal(lineTotal('2', '1.99')!)).toBe('3.98')
    expect(formatDecimal(lineTotal('1.5', '2.333')!)).toBe('3.5')
  })

  it('rounds half away from zero', () => {
    expect(formatDecimal(multiply(d('0.333'), d('0.5')))).toBe('0.167')
    expect(formatDecimal(multiply(d('-0.333'), d('0.5')))).toBe('-0.167')
  })

  it('handles discount lines', () => {
    expect(formatDecimal(sum([lineTotal('1', '3.49')!, lineTotal('1', '-1.00')!]))).toBe('2.49')
  })

  it('returns null for incomplete lines', () => {
    expect(lineTotal('1', '')).toBeNull()
  })
})
