import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessages, getSummary, type Summary } from '../api/client'
import { CategoryBreakdown } from '../components/dashboard/CategoryBreakdown'
import { MonthlyTrend } from '../components/dashboard/MonthlyTrend'
import { NotableChanges } from '../components/dashboard/NotableChanges'
import { SpendingHero } from '../components/dashboard/SpendingHero'
import { TopLists } from '../components/dashboard/TopLists'
import { useCategories } from '../hooks/useCategories'
import { formatMonth, todayIso } from '../lib/format'

function shiftMonth(month: string, delta: number): string {
  const [year, number] = month.split('-').map(Number)
  const index = year * 12 + (number - 1) + delta
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`
}

export function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const thisMonth = todayIso().slice(0, 7)
  const month = searchParams.get('month') ?? thisMonth
  const currency = searchParams.get('currency') ?? ''
  const categories = useCategories()

  const queryKey = `${month}|${currency}`
  const [loaded, setLoaded] = useState<{ key: string; summary: Summary } | null>(null)
  const [error, setError] = useState<{ key: string; messages: string[] } | null>(null)

  useEffect(() => {
    let active = true
    getSummary(month, currency || undefined)
      .then((summary) => {
        if (!active) return
        setLoaded({ key: queryKey, summary })
        setError(null)
      })
      .catch((reason: unknown) => {
        if (active) setError({ key: queryKey, messages: errorMessages(reason) })
      })
    return () => {
      active = false
    }
  }, [month, currency, queryKey])

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next, { replace: true })
  }

  // While a new month loads, the previous one stays on screen, dimmed (no layout jump).
  const summary = loaded?.summary ?? null
  const refreshing = loaded !== null && loaded.key !== queryKey && error?.key !== queryKey

  return (
    <section className="page dashboard">
      <div className="page-title">
        <h1>Spending patterns</h1>
      </div>

      <div className="filters" role="group" aria-label="Dashboard filters">
        <div className="month-nav">
          <button
            type="button"
            className="button button-secondary button-small"
            onClick={() => setParam('month', shiftMonth(month, -1))}
            aria-label="Previous month"
          >
            ‹
          </button>
          <label className="sr-only" htmlFor="dashboard-month">
            Month
          </label>
          <input
            id="dashboard-month"
            type="month"
            value={month}
            max={thisMonth}
            onChange={(event) => setParam('month', event.target.value)}
          />
          <button
            type="button"
            className="button button-secondary button-small"
            onClick={() => setParam('month', shiftMonth(month, 1))}
            disabled={month >= thisMonth}
            aria-label="Next month"
          >
            ›
          </button>
        </div>
        {summary && summary.currencies.length > 1 && (
          <label className="filter-compact">
            Currency
            <select value={summary.currency} onChange={(event) => setParam('currency', event.target.value)}>
              {summary.currencies.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error?.key === queryKey &&
        error.messages.map((message) => (
          <p key={message} className="notice notice-error" role="alert">
            {message}
          </p>
        ))}

      {!summary && !error && <p className="muted">Loading your spending…</p>}

      {summary && (
        <div className={`dashboard-body${refreshing ? ' refreshing' : ''}`} aria-busy={refreshing}>
          <p className="period-note muted">
            {formatMonth(summary.period.month)}
            {summary.period.partial && ` · day 1–${Number(summary.period.end.slice(8))}, compared with the same days of earlier months`}
            {' · '}
            {summary.currency}
          </p>
          {summary.receipt_count === 0 && summary.average_total === null ? (
            <div className="card empty">
              <p>No receipts in {summary.currency} for this month yet.</p>
              <Link to="/scan" className="button button-primary">
                📷 Scan a receipt
              </Link>
            </div>
          ) : (
            <>
              <div className="card-grid">
                <SpendingHero summary={summary} />
                <NotableChanges summary={summary} categories={categories} />
              </div>
              <CategoryBreakdown summary={summary} categories={categories} />
              <MonthlyTrend summary={summary} />
              <TopLists summary={summary} />
            </>
          )}
        </div>
      )}
    </section>
  )
}
