// Money math on decimal strings without floats (CLAUDE.md: never use floats for totals).
// Values are bigints scaled by 10^SCALE; SCALE matches the backend's 3-decimal limit.

const SCALE = 3
const FACTOR = 10n ** BigInt(SCALE)

/** Parses "12", "-1.5", ".25" into a scaled bigint. Returns null for anything else. */
export function parseDecimal(text: string): bigint | null {
  const match = /^(-)?(\d*)(?:\.(\d*))?$/.exec(text.trim())
  if (match === null) return null
  const [, sign, whole, fraction = ''] = match
  if (whole === '' && fraction === '') return null
  if (fraction.length > SCALE) return null
  const value = BigInt((whole || '0') + fraction.padEnd(SCALE, '0'))
  return sign ? -value : value
}

/** Formats a scaled bigint as a plain decimal string, without trailing zeros ("7.47", "10.5", "3"). */
export function formatDecimal(value: bigint): string {
  const negative = value < 0n
  const digits = (negative ? -value : value).toString().padStart(SCALE + 1, '0')
  const whole = digits.slice(0, -SCALE)
  const fraction = digits.slice(-SCALE).replace(/0+$/, '')
  return `${negative ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`
}

/** Multiplies two scaled values, rounding half away from zero. */
export function multiply(a: bigint, b: bigint): bigint {
  const product = a * b
  const half = FACTOR / 2n
  return product >= 0n ? (product + half) / FACTOR : (product - half) / FACTOR
}

export function sum(values: bigint[]): bigint {
  return values.reduce((total, value) => total + value, 0n)
}

/** quantity × price for one receipt line, or null if either is not a valid number. */
export function lineTotal(quantity: string, price: string): bigint | null {
  const q = parseDecimal(quantity)
  const p = parseDecimal(price)
  return q === null || p === null ? null : multiply(q, p)
}

/** Percentage change from `before` to `after`, rounded to a whole number. Null if `before` is 0 or invalid. */
export function percentChange(after: string, before: string): number | null {
  const a = parseDecimal(after)
  const b = parseDecimal(before)
  if (a === null || b === null || b === 0n) return null
  const tenths = ((a - b) * 1000n) / (b < 0n ? -b : b)
  const rounded = tenths >= 0n ? (tenths + 5n) / 10n : (tenths - 5n) / 10n
  return Number(rounded)
}
