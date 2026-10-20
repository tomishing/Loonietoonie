import { Link } from 'react-router'
import { rememberHouseholdPromptDismissed } from '../../lib/householdPrompt'
import type { ComparisonRow, HouseholdComparison as Comparison } from '../../api/client'
import type { CategoriesState } from '../../hooks/useCategories'
import { parseDecimal } from '../../lib/decimal'
import { formatDate, formatMoney } from '../../lib/format'
import { categoryEmoji } from './labels'

const CURRENCY = 'CAD'

function percentOf(value: string, max: bigint): string {
  const scaled = parseDecimal(value)
  if (scaled === null || max <= 0n || scaled <= 0n) return '0%'
  return `${Number((scaled * 10000n) / max) / 100}%`
}

function versus(row: ComparisonRow): string {
  const percent = Math.round(Number(row.difference) * 100)
  if (Math.abs(percent) < 5) return 'about the same'
  return percent > 0 ? `${percent}% more` : `${-percent}% less`
}

function emoji(categories: CategoriesState, label: string): string {
  return label === 'Entertainment & Hobbies' ? categoryEmoji(categories, 'Entertainment') : categoryEmoji(categories, label)
}

/** Patterns: monthly spending per category next to similar households (Statistics Canada).
 * `promptShown`: the banner at the top already invites the user, so the card stays out. */
export function HouseholdComparison({
  comparison,
  categories,
  promptShown,
}: {
  comparison: Comparison | null
  categories: CategoriesState
  promptShown: boolean
}) {
  if (comparison === null || comparison.status === 'no_history') return null

  if (comparison.status === 'not_set') {
    if (promptShown) return null
    return (
      <section className="card" aria-labelledby="household-compare-title">
        <h2 id="household-compare-title" className="card-title">
          Compared with similar households
        </h2>
        <p className="muted">
          See how your spending compares with households like yours in your province, based on Statistics Canada's
          averages.
        </p>
        <Link to="/account#household" className="button button-secondary">
          Tell us about your household
        </Link>
      </section>
    )
  }

  return <ComparisonCard comparison={comparison} categories={categories} />
}

function ComparisonCard({ comparison, categories }: { comparison: Comparison; categories: CategoriesState }) {
  const rows = comparison.rows
  const max = rows.reduce((top, row) => {
    const values = [parseDecimal(row.yours) ?? 0n, parseDecimal(row.typical) ?? 0n]
    return values.reduce((a, b) => (b > a ? b : a), top)
  }, 0n)
  const start = comparison.start ?? ''
  const since = start.endsWith('-01-01') ? 'Jan 1' : formatDate(start)
  const yours = `since ${since}`

  return (
    <section className="card" aria-labelledby="household-compare-title">
      <div className="card-head">
        <h2 id="household-compare-title" className="card-title">
          Compared with similar households
        </h2>
        <p className="legend" aria-hidden="true">
          <span className="legend-item">
            <span className="swatch swatch-bar" /> You, since {since}
          </span>
          <span className="legend-item">
            <span className="swatch swatch-tick" /> Similar households
          </span>
        </p>
      </div>
      <p className="muted">
        {comparison.household}, {comparison.region}. What you've spent from {formatDate(start)} to today, next to the
        same share of a typical year (Statistics Canada's {comparison.survey_year} averages, in {comparison.price_year}{' '}
        prices; seasonal spending isn't modelled).
      </p>
      <ul className="category-bars">
        {rows.map((row) => {
          const description = `${row.category}: ${formatMoney(row.yours, CURRENCY)} ${yours}, similar households ${formatMoney(row.typical, CURRENCY)} (${versus(row)})`
          return (
            <li key={row.category} className="category-item">
              <div className="category-bar" tabIndex={0} aria-label={description}>
                <span className="category-name">
                  <span aria-hidden="true">{emoji(categories, row.category)}</span> {row.category}
                </span>
                <span className="bar-track" aria-hidden="true">
                  <span className="bar-fill" style={{ width: percentOf(row.yours, max) }} />
                  <span className="bar-tick" style={{ left: percentOf(row.typical, max) }} />
                </span>
                <span className="category-amount">{formatMoney(row.yours, CURRENCY)}</span>
                <span className="category-delta">{versus(row)}</span>
                <span className="bar-tooltip" role="tooltip">
                  <strong>{formatMoney(row.yours, CURRENCY)}</strong> {yours}
                  <br />
                  <strong>{formatMoney(row.typical, CURRENCY)}</strong> similar households
                </span>
              </div>
            </li>
          )
        })}
      </ul>
      <p className="muted comparison-note">
        LoonieToonie only sees what you scan or link, so cash and unlinked cards make your amounts look lower. Averages
        are estimates, not targets. <Link to="/account#household">Change your household</Link>
      </p>
    </section>
  )
}

/** Top of Patterns, until the household is set or the user says "Not now" (on this device). */
export function HouseholdPrompt({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="notice notice-info household-prompt" role="status">
      <p>
        <strong>Compare with similar households.</strong> Tell us who lives with you and which province you live in, and
        Patterns will show how your spending compares with similar households there (Statistics Canada averages).
      </p>
      <div className="household-prompt-actions">
        <Link to="/account#household" className="button button-primary button-small">
          Set up
        </Link>
        <button
          type="button"
          className="button button-ghost button-small"
          onClick={() => {
            rememberHouseholdPromptDismissed()
            onDismiss()
          }}
        >
          Not now
        </button>
      </div>
    </div>
  )
}
