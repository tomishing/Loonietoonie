import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'
import { errorMessages, listReceipts, type Receipt } from '../api/client'
import { categoryIcon, useCategories, type CategoriesState } from '../hooks/useCategories'
import { formatDecimal, lineTotal, parseDecimal } from '../lib/decimal'
import { formatDate, formatMoney, formatMonth, monthRange, todayIso } from '../lib/format'

type Result = { status: 'loaded'; receipts: Receipt[] } | { status: 'error'; messages: string[] }
type LoadState = { status: 'loading' } | Result

/** Router state set after saving a receipt. */
export interface TransactionsLocationState {
  highlight?: string
  notice?: string
}

/** Sum of receipt totals per currency (totals in different currencies are never added together). */
function totalsByCurrency(receipts: Receipt[]): string {
  const totals = new Map<string, bigint>()
  for (const receipt of receipts) {
    const amount = parseDecimal(receipt.total) ?? 0n
    totals.set(receipt.currency, (totals.get(receipt.currency) ?? 0n) + amount)
  }
  return [...totals.entries()].map(([currency, total]) => formatMoney(formatDecimal(total), currency)).join(' + ')
}

/** Receipts (already newest first) grouped by purchase month. */
function groupByMonth(receipts: Receipt[]): [string, Receipt[]][] {
  const groups = new Map<string, Receipt[]>()
  for (const receipt of receipts) {
    const month = receipt.purchased_at.slice(0, 7)
    groups.set(month, [...(groups.get(month) ?? []), receipt])
  }
  return [...groups.entries()]
}

export function TransactionsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  // No month in the URL means all receipts, newest first.
  const month = searchParams.get('month') ?? ''
  const category = searchParams.get('category') ?? ''
  const categories = useCategories()
  const location = useLocation()
  const { highlight, notice } = (location.state as TransactionsLocationState | null) ?? {}

  // The result remembers which filters it belongs to; a different filter means "loading".
  const queryKey = `${month}|${category}`
  const [result, setResult] = useState<{ key: string; result: Result } | null>(null)
  const state: LoadState = result?.key === queryKey ? result.result : { status: 'loading' }

  useEffect(() => {
    let active = true
    const range = month ? monthRange(month) : null
    listReceipts({ from: range?.from, to: range?.to, category: category || undefined })
      .then((receipts) => {
        if (active) setResult({ key: queryKey, result: { status: 'loaded', receipts } })
      })
      .catch((error: unknown) => {
        if (active) setResult({ key: queryKey, result: { status: 'error', messages: errorMessages(error) } })
      })
    return () => {
      active = false
    }
  }, [month, category, queryKey])

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next, { replace: true })
  }

  return (
    <section className="page">
      <div className="page-title">
        <h1>Transactions</h1>
        <Link to="/scan" className="button button-primary">
          📷 Scan
        </Link>
      </div>

      {notice && (
        <p className="notice notice-warn" role="status">
          {notice}
        </p>
      )}

      <div className="filters">
        <label>
          Month
          <input type="month" value={month} onChange={(event) => setFilter('month', event.target.value)} />
        </label>
        <div className="filter-buttons">
          <button
            type="button"
            className={`button button-small ${month ? 'button-secondary' : 'button-primary'}`}
            onClick={() => setFilter('month', '')}
          >
            All
          </button>
          <button
            type="button"
            className={`button button-small ${month === todayIso().slice(0, 7) ? 'button-primary' : 'button-secondary'}`}
            onClick={() => setFilter('month', todayIso().slice(0, 7))}
          >
            This month
          </button>
        </div>
        <label>
          Category
          <select value={category} onChange={(event) => setFilter('category', event.target.value)}>
            <option value="">All categories</option>
            {categories.status === 'loaded' &&
              categories.categories.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.icon} {c.name}
                </option>
              ))}
          </select>
        </label>
      </div>

      {state.status === 'loading' && <p className="muted">Loading receipts…</p>}

      {state.status === 'error' &&
        state.messages.map((message) => (
          <p key={message} className="notice notice-error" role="alert">
            {message}
          </p>
        ))}

      {state.status === 'loaded' && state.receipts.length === 0 && (
        <div className="empty">
          <p>{month || category ? 'No receipts match these filters.' : 'No receipts yet.'}</p>
          <Link to="/scan" className="button button-primary">
            📷 Scan a receipt
          </Link>
        </div>
      )}

      {state.status === 'loaded' && state.receipts.length > 0 && (
        <>
          <p className="summary">
            {state.receipts.length} receipt{state.receipts.length === 1 ? '' : 's'} · {totalsByCurrency(state.receipts)}
          </p>
          {groupByMonth(state.receipts).map(([groupMonth, receipts]) => (
            <section key={groupMonth} className="month-group" aria-label={formatMonth(groupMonth)}>
              <h2 className="month-heading">
                <span>{formatMonth(groupMonth)}</span>
                <span>{totalsByCurrency(receipts)}</span>
              </h2>
              <ul className="receipts">
                {receipts.map((receipt) => (
                  <ReceiptCard
                    key={receipt.receipt_id}
                    receipt={receipt}
                    categories={categories}
                    highlighted={receipt.receipt_id === highlight}
                  />
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </section>
  )
}

function ReceiptCard({
  receipt,
  categories,
  highlighted,
}: {
  receipt: Receipt
  categories: CategoriesState
  highlighted: boolean
}) {
  const ref = useRef<HTMLLIElement>(null)

  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [highlighted])

  return (
    <li ref={ref} className={`receipt-card${highlighted ? ' receipt-card-new' : ''}`}>
      <div className="receipt-head">
        <div>
          <p className="receipt-store">
            {receipt.store}
            {highlighted && <span className="badge">Saved</span>}
          </p>
          <p className="muted">{formatDate(receipt.purchased_at)}</p>
        </div>
        <div className="receipt-total">
          <strong>{formatMoney(receipt.total, receipt.currency)}</strong>
          <Link to={`/receipts/${receipt.receipt_id}/edit`} className="edit-link">
            Edit
          </Link>
          {receipt.image_link && (
            <a href={receipt.image_link} target="_blank" rel="noreferrer" className="photo-link">
              📷 Photo
            </a>
          )}
        </div>
      </div>
      <ul className="items">
        {receipt.items.map((item) => {
          const line = lineTotal(item.quantity, item.price)
          return (
            <li key={item.item_id} className="item-row">
              <span className="item-name">
                <span aria-hidden="true">{categoryIcon(categories, item.category)}</span> {item.name}
                {item.quantity !== '1' && <span className="muted"> × {item.quantity}</span>}
              </span>
              <span className="chip chip-small">{item.category}</span>
              <span className="item-price">
                {line === null ? item.price : formatMoney(formatDecimal(line), receipt.currency)}
              </span>
            </li>
          )
        })}
      </ul>
    </li>
  )
}
