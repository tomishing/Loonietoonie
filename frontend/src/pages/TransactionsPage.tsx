import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessages, listReceipts, type Receipt } from '../api/client'
import { categoryIcon, useCategories } from '../hooks/useCategories'
import { formatDecimal, lineTotal, parseDecimal } from '../lib/decimal'
import { formatDate, formatMoney, monthRange, todayIso } from '../lib/format'

type Result = { status: 'loaded'; receipts: Receipt[] } | { status: 'error'; messages: string[] }
type LoadState = { status: 'loading' } | Result

const ALL = 'all'

/** Sum of receipt totals per currency (totals in different currencies are never added together). */
function totalsByCurrency(receipts: Receipt[]): [string, string][] {
  const totals = new Map<string, bigint>()
  for (const receipt of receipts) {
    const amount = parseDecimal(receipt.total) ?? 0n
    totals.set(receipt.currency, (totals.get(receipt.currency) ?? 0n) + amount)
  }
  return [...totals.entries()].map(([currency, total]) => [currency, formatDecimal(total)])
}

export function TransactionsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const month = searchParams.get('month') ?? todayIso().slice(0, 7)
  const category = searchParams.get('category') ?? ''
  const categories = useCategories()
  // The result remembers which filters it belongs to; a different filter means "loading".
  const queryKey = `${month}|${category}`
  const [result, setResult] = useState<{ key: string; result: Result } | null>(null)
  const state: LoadState = result?.key === queryKey ? result.result : { status: 'loading' }

  useEffect(() => {
    let active = true
    const range = month === ALL ? null : monthRange(month)
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

  const totals = state.status === 'loaded' ? totalsByCurrency(state.receipts) : []

  return (
    <section className="page">
      <div className="page-title">
        <h1>Transactions</h1>
        <Link to="/receipts/new" className="button button-primary">
          + Add
        </Link>
      </div>

      <div className="filters">
        <label>
          Month
          <input
            type="month"
            value={month === ALL ? '' : month}
            onChange={(event) => setFilter('month', event.target.value || ALL)}
          />
        </label>
        <button
          type="button"
          className={`button button-small ${month === ALL ? 'button-primary' : 'button-secondary'}`}
          onClick={() => setFilter('month', month === ALL ? '' : ALL)}
        >
          All time
        </button>
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
          <p>No receipts here yet.</p>
          <Link to="/receipts/new" className="button button-primary">
            + Add a receipt
          </Link>
        </div>
      )}

      {state.status === 'loaded' && state.receipts.length > 0 && (
        <>
          <p className="summary">
            {state.receipts.length} receipt{state.receipts.length === 1 ? '' : 's'} ·{' '}
            {totals.map(([currency, total]) => formatMoney(total, currency)).join(' + ')}
          </p>
          <ul className="receipts">
            {state.receipts.map((receipt) => (
              <li key={receipt.receipt_id} className="receipt-card">
                <div className="receipt-head">
                  <div>
                    <p className="receipt-store">{receipt.store}</p>
                    <p className="muted">{formatDate(receipt.purchased_at)}</p>
                  </div>
                  <div className="receipt-total">
                    <strong>{formatMoney(receipt.total, receipt.currency)}</strong>
                    <Link to={`/receipts/${receipt.receipt_id}/edit`} className="edit-link">
                      Edit
                    </Link>
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
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
