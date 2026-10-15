import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'
import {
  errorMessages,
  exportCsvUrl,
  getBankStatus,
  listReceipts,
  shareCsvExport,
  type BankStatus,
  type Receipt,
} from '../api/client'
import { BankTransactionList } from '../components/BankTransactionList'
import { Icon } from '../components/Icon'
import { isNativeApp } from '../platform'
import { categoryIcon, useCategories, type CategoriesState } from '../hooks/useCategories'
import { formatDecimal, lineTotal, parseDecimal } from '../lib/decimal'
import { openPicker } from '../lib/picker'
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
  const subcategory = searchParams.get('subcategory') ?? ''
  // Receipts (scanned or typed in), or transactions imported from linked banks and cards.
  const view = searchParams.get('view') === 'bank' ? 'bank' : 'receipts'
  const [bank, setBank] = useState<BankStatus | null>(null)
  const categories = useCategories()
  const location = useLocation()
  const { highlight, notice } = (location.state as TransactionsLocationState | null) ?? {}

  // The result remembers which filters it belongs to; a different filter means "loading".
  const queryKey = `${month}|${category}|${subcategory}`
  const [result, setResult] = useState<{ key: string; result: Result } | null>(null)
  const state: LoadState = result?.key === queryKey ? result.result : { status: 'loading' }

  const range = month ? monthRange(month) : null
  const filters = {
    from: range?.from,
    to: range?.to,
    category: category || undefined,
    subcategory: subcategory || undefined,
  }
  const subcategoryOptions =
    categories.status === 'loaded' && category
      ? (categories.categories.find((c) => c.name === category)?.subcategories ?? [])
      : []

  useEffect(() => {
    let active = true
    getBankStatus()
      .then((status) => active && setBank(status))
      .catch(() => undefined) // without it, only receipts are shown
    return () => {
      active = false
    }
  }, [])
  const hasBanks = bank !== null && bank.enabled && bank.items.length > 0
  const accounts = bank?.items.flatMap((item) => item.accounts) ?? []

  useEffect(() => {
    if (view !== 'receipts') return
    let active = true
    const range = month ? monthRange(month) : null
    listReceipts({
      from: range?.from,
      to: range?.to,
      category: category || undefined,
      subcategory: subcategory || undefined,
    })
      .then((receipts) => {
        if (active) setResult({ key: queryKey, result: { status: 'loaded', receipts } })
      })
      .catch((error: unknown) => {
        if (active) setResult({ key: queryKey, result: { status: 'error', messages: errorMessages(error) } })
      })
    return () => {
      active = false
    }
  }, [month, category, subcategory, queryKey, view])

  // The app's WebView can't download with the session token, so it fetches and shares the file instead.
  const shareOnApp = (event: MouseEvent, kind: 'items' | 'receipts') => {
    if (!isNativeApp) return
    event.preventDefault()
    shareCsvExport(kind, filters).catch(() => window.alert("Couldn't export the CSV. Please try again."))
  }

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    // Subcategories belong to one category.
    if (key === 'category') next.delete('subcategory')
    setSearchParams(next, { replace: true })
  }

  const thisMonth = todayIso().slice(0, 7)

  return (
    <section className="page">
      <div className="page-title">
        <h1>Transactions</h1>
        <Link to="/receipts/new" className="button button-secondary button-small">
          <Icon name="plus" size={18} />
          Add
        </Link>
      </div>

      {notice && (
        <p className="notice notice-warn" role="status">
          {notice}
        </p>
      )}

      {(hasBanks || view === 'bank') && (
        <div className="segmented" role="tablist" aria-label="Show">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'receipts'}
            className={view === 'receipts' ? 'segment segment-on' : 'segment'}
            onClick={() => setFilter('view', '')}
          >
            <Icon name="list" size={18} />
            Receipts
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'bank'}
            className={view === 'bank' ? 'segment segment-on' : 'segment'}
            onClick={() => setFilter('view', 'bank')}
          >
            <Icon name="card" size={18} />
            Bank & card
          </button>
        </div>
      )}

      <div className="chip-row" role="group" aria-label="Filters">
        <button
          type="button"
          className={`chip chip-toggle${month ? '' : ' chip-on'}`}
          aria-pressed={!month}
          onClick={() => setFilter('month', '')}
        >
          All
        </button>
        <button
          type="button"
          className={`chip chip-toggle${month === thisMonth ? ' chip-on' : ''}`}
          aria-pressed={month === thisMonth}
          onClick={() => setFilter('month', thisMonth)}
        >
          This month
        </button>
        <label className={`chip chip-toggle chip-picker${month && month !== thisMonth ? ' chip-on' : ''}`}>
          {month && month !== thisMonth ? formatMonth(month) : 'Pick a month'}
          <Icon name="down" size={16} />
          <input
            type="month"
            className="month-switch-input"
            aria-label="Month"
            value={month}
            max={thisMonth}
            onClick={openPicker}
            onChange={(event) => setFilter('month', event.target.value)}
          />
        </label>
        <label className={`chip-select${category ? ' chip-on' : ''}`}>
          <span className="sr-only">Category</span>
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
        {view === 'receipts' && subcategoryOptions.length > 0 && (
          <label className={`chip-select${subcategory ? ' chip-on' : ''}`}>
            <span className="sr-only">Subcategory</span>
            <select value={subcategory} onChange={(event) => setFilter('subcategory', event.target.value)}>
              <option value="">All of {category}</option>
              {subcategoryOptions.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {view === 'bank' ? (
        <BankTransactionList month={month} category={category} accounts={accounts} categories={categories} />
      ) : (
        <>
        {state.status === 'loading' && (
          <div aria-hidden="true">
            <div className="skeleton skeleton-row" />
            <div className="skeleton skeleton-row" />
            <div className="skeleton skeleton-row" />
          </div>
        )}

        {state.status === 'error' &&
          state.messages.map((message) => (
            <p key={message} className="notice notice-error" role="alert">
              {message}
            </p>
          ))}

        {state.status === 'loaded' && state.receipts.length === 0 && (
          <div className="card empty">
            <img src="/favicon.svg" alt="" width={64} height={64} />
            <p>{month || category || subcategory ? 'No receipts match these filters.' : 'No receipts yet.'}</p>
            <Link to="/scan" className="button button-primary">
              <Icon name="camera" size={20} />
              Scan a receipt
            </Link>
          </div>
        )}

        {state.status === 'loaded' && state.receipts.length > 0 && (
          <>
            <div className="summary-row">
              <p className="summary">
                <span className="summary-total">{totalsByCurrency(state.receipts)}</span>
                <span className="muted">
                  {state.receipts.length} receipt{state.receipts.length === 1 ? '' : 's'}
                </span>
              </p>
              <p className="export-links">
                <span className="sr-only">Export CSV:</span>
                <a
                  className="chip chip-action"
                  href={exportCsvUrl('items', filters)}
                  download
                  onClick={(event) => shareOnApp(event, 'items')}
                >
                  <Icon name="download" size={16} />
                  Items CSV
                </a>
                <a
                  className="chip chip-action"
                  href={exportCsvUrl('receipts', filters)}
                  download
                  onClick={(event) => shareOnApp(event, 'receipts')}
                >
                  <Icon name="download" size={16} />
                  Receipts CSV
                </a>
              </p>
            </div>
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

  const count = receipt.items.length
  const mainCategory = receipt.items[0]?.category

  return (
    <li ref={ref} className={`receipt-card${highlighted ? ' receipt-card-new' : ''}`}>
      <details open={highlighted}>
        <summary className="receipt-head">
          <span className="receipt-avatar" aria-hidden="true">
            {mainCategory ? categoryIcon(categories, mainCategory) : '🧾'}
          </span>
          <span className="receipt-info">
            <span className="receipt-store">
              {receipt.store}
              {highlighted && <span className="badge">Saved</span>}
            </span>
            <span className="muted">
              {formatDate(receipt.purchased_at)} · {count} item{count === 1 ? '' : 's'}
            </span>
          </span>
          <strong className="receipt-amount">{formatMoney(receipt.total, receipt.currency)}</strong>
          <Icon name="down" size={18} className="receipt-toggle" />
        </summary>
        <ul className="items">
          {receipt.items.map((item) => {
            const line = lineTotal(item.quantity, item.price)
            return (
              <li key={item.item_id} className="item-row">
                <span className="item-name">
                  <span aria-hidden="true">{categoryIcon(categories, item.category)}</span> {item.name}
                  {item.quantity !== '1' && <span className="muted"> × {item.quantity}</span>}
                </span>
                <span className="chip chip-small" title={item.subcategory ? `${item.category} › ${item.subcategory}` : undefined}>
                  {item.subcategory || item.category}
                </span>
                <span className="item-price">
                  {line === null ? item.price : formatMoney(formatDecimal(line), receipt.currency)}
                </span>
              </li>
            )
          })}
        </ul>
        <div className="receipt-actions">
          <Link to={`/receipts/${receipt.receipt_id}/edit`} className="chip chip-action">
            <Icon name="pencil" size={16} />
            Edit
          </Link>
          {receipt.image_link && (
            <a href={receipt.image_link} target="_blank" rel="noreferrer" className="chip chip-action">
              <Icon name="image" size={16} />
              Photo
            </a>
          )}
        </div>
      </details>
    </li>
  )
}
