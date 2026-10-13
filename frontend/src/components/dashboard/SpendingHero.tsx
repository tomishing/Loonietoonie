import type { Summary } from '../../api/client'
import { percentChange } from '../../lib/decimal'
import { formatMoney } from '../../lib/format'

function Delta({ now, before, label }: { now: string; before: string | null; label: string }) {
  if (before === null) return null
  const change = percentChange(now, before)
  return (
    <p className="delta">
      {change === null ? (
        <span>Nothing spent {label}</span>
      ) : (
        <>
          <span className="delta-value">
            <span aria-hidden="true">{change > 0 ? '▲' : change < 0 ? '▼' : '='}</span> {Math.abs(change)}%{' '}
            {change > 0 ? 'more' : change < 0 ? 'less' : 'same'}
          </span>{' '}
          <span className="muted-inline">than {label}</span>
        </>
      )}
    </p>
  )
}

export function SpendingHero({ summary }: { summary: Summary }) {
  const { period, currency } = summary
  const lastMonthLabel = period.partial ? 'the same days last month' : 'last month'
  const averageLabel = period.partial ? 'the same days of your usual month' : 'your usual month'

  return (
    <section className="card hero" aria-label="Total spending">
      <p className="card-label">
        Spent {period.partial ? 'so far this month' : 'this month'}
        {summary.receipt_count > 0 && (
          <span className="muted-inline">
            {' '}
            · {summary.receipt_count} receipt{summary.receipt_count === 1 ? '' : 's'}
          </span>
        )}
      </p>
      <p className="hero-figure">{formatMoney(summary.total, currency)}</p>
      <Delta now={summary.total} before={summary.previous_total} label={lastMonthLabel} />
      <Delta now={summary.total} before={summary.average_total} label={averageLabel} />
      {summary.average_total === null && (
        <p className="muted">Comparisons appear once you have receipts from earlier months.</p>
      )}
    </section>
  )
}
