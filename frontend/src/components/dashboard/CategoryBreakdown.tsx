import { useState } from 'react'
import type { CategorySpending, SubcategorySpending, Summary } from '../../api/client'
import { Icon } from '../Icon'
import type { CategoriesState } from '../../hooks/useCategories'
import { parseDecimal, percentChange } from '../../lib/decimal'
import { formatMoney } from '../../lib/format'
import { categoryEmoji, categoryLabel } from './labels'

/** Position of `value` on a 0–`max` scale, as a CSS percentage. */
function percentOf(value: string | null, max: bigint): string {
  const scaled = value === null ? null : parseDecimal(value)
  if (scaled === null || max <= 0n || scaled <= 0n) return '0%'
  return `${Number((scaled * 10000n) / max) / 100}%`
}

function versusUsual(row: CategorySpending | SubcategorySpending): string | null {
  if (row.average_total === null) return null
  const change = percentChange(row.total, row.average_total)
  if (change === null) return 'new'
  if (change === 0) return 'as usual'
  return `${change > 0 ? '▲' : '▼'} ${Math.abs(change)}%`
}

export function CategoryBreakdown({ summary, categories }: { summary: Summary; categories: CategoriesState }) {
  const rows = summary.categories
  const { currency } = summary
  // One shared scale for this month's bars and the "usual" ticks.
  const max = rows.reduce((largest, row) => {
    const values = [row.total, row.average_total ?? '0'].map((v) => parseDecimal(v) ?? 0n)
    return values.reduce((a, b) => (b > a ? b : a), largest)
  }, 0n)
  const hasAverage = summary.average_total !== null
  const [open, setOpen] = useState<string | null>(null)
  const canDrillDown = rows.some((row) => row.subcategories.length > 0)

  return (
    <section className="card" aria-labelledby="categories-title">
      <div className="card-head">
        <h2 id="categories-title" className="card-title">
          By category
        </h2>
        {hasAverage && (
          <p className="legend" aria-hidden="true">
            <span className="legend-item">
              <span className="swatch swatch-bar" /> This month
            </span>
            <span className="legend-item">
              <span className="swatch swatch-tick" /> Usual month
            </span>
          </p>
        )}
      </div>
      {canDrillDown && <p className="muted drill-hint">Tap a category to see its subcategories.</p>}

      {rows.length === 0 ? (
        <p className="muted">No spending in this month.</p>
      ) : (
        <ul className="category-bars">
          {rows.map((row) => {
            const usual = versusUsual(row)
            const description = [
              `${categoryLabel(row.category)}: ${formatMoney(row.total, currency)} this month`,
              `${formatMoney(row.previous_total, currency)} last month`,
              row.average_total !== null ? `${formatMoney(row.average_total, currency)} in a usual month` : null,
            ]
              .filter(Boolean)
              .join(', ')
            const expandable = row.subcategories.length > 0
            const expanded = expandable && open === row.category
            const content = (
              <>
                <span className="category-name">
                  <span aria-hidden="true">{categoryEmoji(categories, row.category)}</span>{' '}
                  {categoryLabel(row.category)}
                  {expandable && <Icon name="down" size={16} className="drill-chevron" />}
                </span>
                <span className="bar-track" aria-hidden="true">
                  <span className="bar-fill" style={{ width: percentOf(row.total, max) }} />
                  {row.average_total !== null && (parseDecimal(row.average_total) ?? 0n) > 0n && (
                    <span className="bar-tick" style={{ left: percentOf(row.average_total, max) }} />
                  )}
                </span>
                <span className="category-amount">{formatMoney(row.total, currency)}</span>
                <span className="category-delta">{usual ?? ''}</span>
                <span className="bar-tooltip" role="tooltip">
                  <strong>{formatMoney(row.total, currency)}</strong> this month
                  <br />
                  <strong>{formatMoney(row.previous_total, currency)}</strong> last month
                  {row.average_total !== null && (
                    <>
                      <br />
                      <strong>{formatMoney(row.average_total, currency)}</strong> usual month
                    </>
                  )}
                </span>
              </>
            )
            return (
              <li key={row.category} className={expanded ? 'category-item category-item-open' : 'category-item'}>
                {expandable ? (
                  <button
                    type="button"
                    className="category-bar"
                    aria-label={`${description}. ${expanded ? 'Hide' : 'Show'} subcategories`}
                    aria-expanded={expanded}
                    onClick={() => setOpen(expanded ? null : row.category)}
                  >
                    {content}
                  </button>
                ) : (
                  <div className="category-bar" tabIndex={0} aria-label={description}>
                    {content}
                  </div>
                )}
                {expanded && <SubcategoryBars rows={row.subcategories} currency={currency} />}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

/** One category's subcategories, on their own scale so small ones stay visible. */
function SubcategoryBars({ rows, currency }: { rows: SubcategorySpending[]; currency: string }) {
  const max = rows.reduce((largest, row) => {
    const values = [row.total, row.average_total ?? '0'].map((v) => parseDecimal(v) ?? 0n)
    return values.reduce((a, b) => (b > a ? b : a), largest)
  }, 0n)

  return (
    <ul className="subcategory-bars">
      {rows.map((row) => {
        const name = row.subcategory || 'No subcategory'
        const usual = versusUsual(row)
        return (
          <li
            key={row.subcategory}
            className="category-bar subcategory-bar"
            aria-label={`${name}: ${formatMoney(row.total, currency)} this month${
              row.average_total !== null ? `, ${formatMoney(row.average_total, currency)} in a usual month` : ''
            }`}
          >
            <span className={row.subcategory ? 'category-name' : 'category-name muted-inline'}>{name}</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar-fill" style={{ width: percentOf(row.total, max) }} />
              {row.average_total !== null && (parseDecimal(row.average_total) ?? 0n) > 0n && (
                <span className="bar-tick" style={{ left: percentOf(row.average_total, max) }} />
              )}
            </span>
            <span className="category-amount">{formatMoney(row.total, currency)}</span>
            <span className="category-delta">{usual ?? ''}</span>
          </li>
        )
      })}
    </ul>
  )
}
