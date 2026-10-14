import { describe, expect, it } from 'vitest'
import { chooseLines, linesToText, type OcrLine } from './rows'

const line = (text: string, left: number, top: number, width = 100, height = 20): OcrLine => ({
  text,
  left,
  top,
  right: left + width,
  bottom: top + height,
})

describe('linesToText', () => {
  it('puts item names and prices from separate columns back on one row', () => {
    // ML Kit order: all names first, then all prices.
    const lines = [
      line('FRESHCO', 0, 0),
      line('MILK 2% 4L', 0, 40),
      line('BANANAS', 0, 70),
      line('TOTAL', 0, 100),
      line('6.49', 300, 41, 50),
      line('1.49', 300, 69, 50),
      line('7.98', 300, 102, 50),
    ]

    expect(linesToText(lines)).toBe('FRESHCO\nMILK 2% 4L    6.49\nBANANAS    1.49\nTOTAL    7.98')
  })

  it('tolerates slightly tilted photos', () => {
    expect(linesToText([line('Soap', 0, 50), line('4.99', 300, 58, 40)])).toBe('Soap    4.99')
  })

  it('keeps separate rows apart', () => {
    expect(linesToText([line('A', 0, 0), line('B', 0, 25)])).toBe('A\nB')
  })

  it('ignores empty lines', () => {
    expect(linesToText([line('  ', 0, 0), line('A', 0, 30)])).toBe('A')
    expect(linesToText([])).toBe('')
  })
})

describe('chooseLines', () => {
  const latin = [line('7-ELEVEN', 0, 0)]
  const japanese = [line('セブン-イレブン', 0, 0), line('おにぎり ¥150', 0, 30)]

  it('uses the Japanese reading for Japanese receipts', () => {
    expect(chooseLines(latin, japanese)).toBe(japanese)
  })

  it('uses the Latin reading otherwise (French accents, English)', () => {
    const french = [line('TOTAL À PAYER 31,35', 0, 0)]
    expect(chooseLines(french, [line('TOTAL A PAYER 31,35', 0, 0)])).toBe(french)
  })

  it('falls back to whatever was found', () => {
    expect(chooseLines([], [line('TOTAL 5.00', 0, 0)])).toEqual([line('TOTAL 5.00', 0, 0)])
  })
})
