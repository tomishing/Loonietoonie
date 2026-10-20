import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessages, getSummary, type Summary } from '../api/client'
import { CategoryBreakdown } from '../components/dashboard/CategoryBreakdown'
import { HouseholdComparison } from '../components/dashboard/HouseholdComparison'
import { MonthlyTrend } from '../components/dashboard/MonthlyTrend'
import { NotableChanges } from '../components/dashboard/NotableChanges'
import { SpendingHero } from '../components/dashboard/SpendingHero'
import { TopLists } from '../components/dashboard/TopLists'
import { useAuth } from '../auth/AuthContext'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { Icon } from '../components/Icon'
import { MonthSwitch } from '../components/MonthSwitch'
import { useCategories } from '../hooks/useCategories'
import { formatMonth, todayIso } from '../lib/format'

function greeting(): string {
  const hour = new Date().getHours()
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

export function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const thisMonth = todayIso().slice(0, 7)
  const month = searchParams.get('month') ?? thisMonth
  const currency = searchParams.get('currency') ?? ''
  const categories = useCategories()
  const { user } = useAuth()
  const firstName = user?.name?.split(/\s+/)[0]

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
        <div>
          <p className="eyebrow">
            {greeting()}
            {firstName && `, ${firstName}`}
          </p>
          <h1>Spending patterns</h1>
        </div>
      </div>

      <div className="filters" role="group" aria-label="Dashboard filters">
        <MonthSwitch month={month} max={thisMonth} onChange={(value) => setParam('month', value)} />
        {summary && summary.currencies.length > 1 && (
          <label className="chip-select">
            <span className="sr-only">Currency</span>
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

      {!summary && !error && <DashboardSkeleton withTitle={false} />}

      {summary && (
        <div className={`dashboard-body${refreshing ? ' refreshing' : ''}`} aria-busy={refreshing}>
          <p className="period-note muted">
            {formatMonth(summary.period.month)}
            {summary.period.partial && ` · day 1–${Number(summary.period.end.slice(8))}, compared with the same days of earlier months`}
            {' · '}
            {summary.currency}
            {summary.bank_transaction_count > 0 &&
              ` · includes ${summary.bank_transaction_count} card & bank charge${summary.bank_transaction_count === 1 ? '' : 's'} without a receipt`}
          </p>
          {summary.receipt_count === 0 && summary.average_total === null ? (
            <div className="card empty">
              <img src="/favicon.svg" alt="" width={64} height={64} />
              <p>No receipts in {summary.currency} for this month yet.</p>
              <Link to="/scan" className="button button-primary">
                <Icon name="camera" size={20} />
                Scan a receipt
              </Link>
            </div>
          ) : (
            <>
              <div className="card-grid">
                <SpendingHero summary={summary} />
                <NotableChanges summary={summary} categories={categories} />
              </div>
              <CategoryBreakdown summary={summary} categories={categories} />
              {summary.currency === 'CAD' && <HouseholdComparison categories={categories} />}
              <MonthlyTrend summary={summary} />
              <TopLists summary={summary} />
            </>
          )}
        </div>
      )}
    </section>
  )
}
