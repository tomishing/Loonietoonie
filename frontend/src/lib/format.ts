/** Formats a decimal string as money, e.g. formatMoney("7.5", "USD") → "$7.50". */
export function formatMoney(amount: string, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(
      amount as Intl.StringNumericLiteral,
    )
  } catch {
    return `${amount} ${currency}`
  }
}

/** Formats an ISO date (YYYY-MM-DD) for display without time zone shifts. */
export function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number)
  if (!year || !month || !day) return isoDate
  return new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric' }).format(
    new Date(year, month - 1, day),
  )
}

/** Today's date in the user's time zone as YYYY-MM-DD. */
export function todayIso(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** First and last day of a YYYY-MM month as ISO dates. */
export function monthRange(month: string): { from: string; to: string } | null {
  const match = /^(\d{4})-(\d{2})$/.exec(month)
  if (match === null) return null
  const year = Number(match[1])
  const monthNumber = Number(match[2])
  const lastDay = new Date(year, monthNumber, 0).getDate()
  return { from: `${month}-01`, to: `${month}-${String(lastDay).padStart(2, '0')}` }
}
