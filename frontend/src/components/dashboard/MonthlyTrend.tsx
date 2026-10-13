import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Summary, TrendMonth } from '../../api/client'
import { formatMoney, formatMonth } from '../../lib/format'
import { OTHER_CATEGORIES, categoryLabel } from './labels'

// Validated categorical slots (light mode, on the app surface) in fixed order; folded categories are gray.
const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)']

function seriesColor(name: string, index: number): string {
  return name === OTHER_CATEGORIES ? 'var(--series-other)' : SERIES_COLORS[index % SERIES_COLORS.length]
}

function shortMonth(month: string): string {
  const [year, number] = month.split('-').map(Number)
  const label = new Intl.DateTimeFormat(undefined, { month: 'short' }).format(new Date(year, number - 1, 1))
  return number === 1 ? `${label} '${String(year).slice(2)}` : label
}

const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })

interface ChartRow {
  month: string
  [category: string]: string | number
}

function TrendTooltip({
  active,
  label,
  trend,
  names,
  currency,
  partialMonth,
}: {
  active?: boolean
  label?: string | number
  trend: TrendMonth[]
  names: string[]
  currency: string
  partialMonth: string | null
}) {
  const month = trend.find((t) => t.month === label)
  if (!active || !month) return null
  return (
    <div className="chart-tooltip">
      <p className="chart-tooltip-title">
        {formatMonth(month.month)}
        {month.month === partialMonth ? ' (so far)' : ''}
      </p>
      <p className="chart-tooltip-total">
        <strong>{formatMoney(month.total, currency)}</strong> total
      </p>
      <ul>
        {[...names].reverse().map((name) => (
          <li key={name}>
            <span className="line-key" style={{ background: seriesColor(name, names.indexOf(name)) }} />
            <strong>{formatMoney(month.by_category[name] ?? '0', currency)}</strong> {categoryLabel(name)}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function MonthlyTrend({ summary }: { summary: Summary }) {
  const [showTable, setShowTable] = useState(false)
  const { trend, trend_categories: names, currency } = summary
  const partialMonth = summary.period.partial ? summary.period.month : null
  const hasData = trend.some((month) => Number(month.total) !== 0)

  // Numbers are used only to draw; every value shown as text comes from the decimal strings.
  const rows: ChartRow[] = trend.map((month) => ({
    month: month.month,
    ...Object.fromEntries(names.map((name) => [name, Number(month.by_category[name] ?? 0)])),
  }))

  return (
    <section className="card" aria-labelledby="trend-title">
      <div className="card-head">
        <h2 id="trend-title" className="card-title">
          Last 12 months
        </h2>
        {hasData && (
          <button type="button" className="link-button" onClick={() => setShowTable((value) => !value)}>
            {showTable ? 'Show chart' : 'Show table'}
          </button>
        )}
      </div>

      {!hasData ? (
        <p className="muted">No spending in the last 12 months.</p>
      ) : showTable ? (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Month</th>
                {names.map((name) => (
                  <th key={name} scope="col">
                    {categoryLabel(name)}
                  </th>
                ))}
                <th scope="col">Total</th>
              </tr>
            </thead>
            <tbody>
              {[...trend].reverse().map((month) => (
                <tr key={month.month}>
                  <th scope="row">
                    {formatMonth(month.month)}
                    {month.month === partialMonth ? ' (so far)' : ''}
                  </th>
                  {names.map((name) => (
                    <td key={name}>{formatMoney(month.by_category[name] ?? '0', currency)}</td>
                  ))}
                  <td>
                    <strong>{formatMoney(month.total, currency)}</strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <ul className="legend legend-wrap" aria-label="Categories">
            {names.map((name, index) => (
              <li key={name} className="legend-item">
                <span className="swatch" style={{ background: seriesColor(name, index) }} />
                {categoryLabel(name)}
              </li>
            ))}
          </ul>
          <div className="chart" role="img" aria-label={`Monthly spending for the last 12 months, by category, in ${currency}. Use "Show table" for the numbers.`}>
            <ResponsiveContainer width="100%" height={260} minWidth={0}>
              <BarChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis
                  dataKey="month"
                  tickFormatter={shortMonth}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--axis)' }}
                  tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
                  interval="preserveStartEnd"
                  minTickGap={4}
                />
                <YAxis
                  tickFormatter={(value: number) => compact.format(value)}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
                  width={44}
                />
                <Tooltip
                  cursor={{ fill: 'var(--hover-wash)' }}
                  content={(props) => (
                    <TrendTooltip
                      active={props.active}
                      label={props.label}
                      trend={trend}
                      names={names}
                      currency={currency}
                      partialMonth={partialMonth}
                    />
                  )}
                />
                {names.map((name, index) => (
                  <Bar
                    key={name}
                    dataKey={name}
                    stackId="spending"
                    fill={seriesColor(name, index)}
                    stroke="var(--surface)"
                    strokeWidth={2}
                    maxBarSize={24}
                    radius={index === names.length - 1 ? [4, 4, 0, 0] : 0}
                    isAnimationActive={false}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </section>
  )
}
