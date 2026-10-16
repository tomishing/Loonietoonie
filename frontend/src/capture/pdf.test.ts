import { describe, expect, it } from 'vitest'
import { isPdf, piecesToText } from './pdf'

const piece = (str: string, x: number, y: number) => ({ str, transform: [1, 0, 0, 1, x, y] })

describe('piecesToText', () => {
  it('rebuilds lines top to bottom, left to right', () => {
    const text = piecesToText([
      piece('5.49', 300, 700),
      piece('TOTAL', 20, 650),
      piece('Milk 2%', 20, 700.5), // same line as 5.49 (baseline within 2 pt)
      piece('5.49', 300, 650),
      piece('FRESHCO', 20, 760),
      piece('  ', 100, 760),
    ])

    expect(text).toBe('FRESHCO\nMilk 2% 5.49\nTOTAL 5.49')
  })

  it('returns nothing for a scanned page without text', () => {
    expect(piecesToText([])).toBe('')
  })
})

describe('isPdf', () => {
  it('recognizes PDFs by type or file name', () => {
    expect(isPdf(new File([''], 'receipt.PDF'))).toBe(true)
    expect(isPdf(new Blob([''], { type: 'application/pdf' }))).toBe(true)
    expect(isPdf(new File([''], 'receipt.jpg', { type: 'image/jpeg' }))).toBe(false)
  })
})
