import type { Change, Summary } from '../../api/client'
import type { CategoriesState } from '../../hooks/useCategories'
import { percentChange } from '../../lib/decimal'
import { formatMoney } from '../../lib/format'
import { categoryEmoji } from './labels'

function describe(change: Change, currency: string): string {
  const amount = formatMoney(change.amount, currency)
  switch (change.kind) {
    case 'category_up':
    case 'category_down': {
      const percent = Math.abs(percentChange(change.amount, change.baseline ?? '0') ?? 0)
      const usual = formatMoney(change.baseline ?? '0', currency)
      return `${change.category}: ${amount}, ${percent}% ${change.kind === 'category_up' ? 'more' : 'less'} than your usual ${usual}`
    }
    case 'category_new':
      return `${change.category}: ${amount}, new this month`
    case 'new_store':
      return `First time at ${change.store} (${amount})`
  }
}

const MARKS: Record<Change['kind'], string> = {
  category_up: '▲',
  category_down: '▼',
  category_new: '✨',
  new_store: '🏪',
}

export function NotableChanges({ summary, categories }: { summary: Summary; categories: CategoriesState }) {
  if (summary.average_total === null) return null

  return (
    <section className="card" aria-labelledby="changes-title">
      <h2 id="changes-title" className="card-title">
        Notable changes
      </h2>
      {summary.changes.length === 0 ? (
        <p className="muted">Nothing unusual — spending looks like your usual month.</p>
      ) : (
        <ul className="changes">
          {summary.changes.map((change) => (
            <li key={`${change.kind}-${change.category ?? change.store}`}>
              <span className="change-mark" aria-hidden="true">
                {change.category ? categoryEmoji(categories, change.category) : ''} {MARKS[change.kind]}
              </span>
              <span>{describe(change, summary.currency)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
