import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { getHouseholdComparison, type ComparisonRow, type HouseholdComparison as Comparison } from '../../api/client'
import type { CategoriesState } from '../../hooks/useCategories'
import { parseDecimal } from '../../lib/decimal'
import { formatMoney, formatMonth } from '../../lib/format'
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

/** Patterns: monthly spending per category next to similar households (Statistics Canada). */
export function HouseholdComparison({ categories }: { categories: CategoriesState }) {
  const [comparison, setComparison] = useState<Comparison | null>(null)

  useEffect(() => {
    getHouseholdComparison()
      .then(setComparison)
      .catch(() => setComparison(null)) // optional card: leave it out if it can't load
  }, [])

  if (comparison === null || comparison.status === 'no_history') return null

  if (comparison.status === 'not_set') {
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

  const max = comparison.rows.reduce((top, row) => {
    const values = [parseDecimal(row.yours) ?? 0n, parseDecimal(row.typical) ?? 0n]
    return values.reduce((a, b) => (b > a ? b : a), top)
  }, 0n)
  const first = comparison.months[0]
  const last = comparison.months[comparison.months.length - 1]
  const span = first === last ? formatMonth(first) : `${formatMonth(first)} – ${formatMonth(last)}`

  return (
    <section className="card" aria-labelledby="household-compare-title">
      <div className="card-head">
        <h2 id="household-compare-title" className="card-title">
          Compared with similar households
        </h2>
        <p className="legend" aria-hidden="true">
          <span className="legend-item">
            <span className="swatch swatch-bar" /> You, per month
          </span>
          <span className="legend-item">
            <span className="swatch swatch-tick" /> Similar households
          </span>
        </p>
      </div>
      <p className="muted">
        {comparison.household}, {comparison.region}. Your average month ({span}) next to Statistics Canada's{' '}
        {comparison.survey_year} averages, in {comparison.price_year} prices.
      </p>
      <ul className="category-bars">
        {comparison.rows.map((row) => {
          const description = `${row.category}: ${formatMoney(row.yours, CURRENCY)} a month, similar households ${formatMoney(row.typical, CURRENCY)} (${versus(row)})`
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
                  <strong>{formatMoney(row.yours, CURRENCY)}</strong> your average month
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
