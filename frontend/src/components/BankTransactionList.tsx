import { lazy, Suspense, useEffect, useState } from 'react'
import { Link } from 'react-router'
import {
  errorMessages,
  listBankTransactions,
  type BankAccount,
  type BankTransaction,
  type LinkedInstitution,
} from '../api/client'
import { categoryIcon, type CategoriesState } from '../hooks/useCategories'
import { formatDecimal, parseDecimal } from '../lib/decimal'
import { formatDate, formatMoney, formatMonth, monthRange } from '../lib/format'
import { Icon } from './Icon'
import { BankCategoryEditor } from './BankCategoryEditor'
import { ReceiptMatch } from './ReceiptMatch'

type State = { status: 'loading' } | { status: 'loaded'; transactions: BankTransaction[] } | { status: 'error'; messages: string[] }

// Plaid sync is on the web app only (phase 12). `import.meta.env.MODE` is a constant at build time,
// so the Android build (`--mode android`) drops this import and Plaid's code entirely.
const PlaidSync = import.meta.env.MODE !== 'android' ? lazy(() => import('./PlaidSync')) : null

/** Sum of spending (kind "spending") per currency; transfers and income are left out. */
function spendingTotals(transactions: BankTransaction[]): string {
  const totals = new Map<string, bigint>()
  for (const t of transactions) {
    if (t.kind !== 'spending') continue
    totals.set(t.currency, (totals.get(t.currency) ?? 0n) + (parseDecimal(t.amount) ?? 0n))
  }
  return [...totals.entries()].map(([currency, total]) => formatMoney(formatDecimal(total), currency)).join(' + ')
}

function groupByMonth(transactions: BankTransaction[]): [string, BankTransaction[]][] {
  const groups = new Map<string, BankTransaction[]>()
  for (const t of transactions) {
    const month = t.date.slice(0, 7)
    groups.set(month, [...(groups.get(month) ?? []), t])
  }
  return [...groups.entries()]
}

export function BankTransactionList({
  month,
  category,
  accounts,
  categories,
  banks,
  onSynced,
}: {
  month: string
  category: string
  accounts: BankAccount[]
  categories: CategoriesState
  /** Banks linked through Plaid (web app), for syncing and "last read" times; empty when none. */
  banks: LinkedInstitution[]
  /** Called after a Plaid sync (e.g. to reload the banks' status). */
  onSynced: () => void
}) {
  // Bumped after a match changes or a sync, to reload the list.
  const [version, setVersion] = useState(0)
  const reload = () => setVersion((v) => v + 1)
  // The result remembers which filters it belongs to; different filters mean "loading".
  const key = `${month}|${category}`
  const [result, setResult] = useState<{ key: string; state: State } | null>(null)
  const state: State = result?.key === key ? result.state : { status: 'loading' }
  useEffect(() => {
    let active = true
    const range = month ? monthRange(month) : null
    const load = () =>
      listBankTransactions({ from: range?.from, to: range?.to, category: category || undefined })
        .then((transactions) => active && setResult({ key, state: { status: 'loaded', transactions } }))
        .catch(
          (error: unknown) => active && setResult({ key, state: { status: 'error', messages: errorMessages(error) } }),
        )
    void load()
    return () => {
      active = false
    }
  }, [month, category, key, version]) // a reload after a match change keeps the list on screen

  const accountLabel = (id: string) => {
    const account = accounts.find((a) => a.account_id === id)
    if (!account) return ''
    return account.mask ? `${account.name} •• ${account.mask}` : account.name
  }

  // A stable callback, so PlaidSync's first sync runs once.
  const [afterSync] = useState(() => () => {
    setVersion((v) => v + 1)
    onSynced()
  })

  const bar = (
    <>
      <div className="bank-sync-bar">
        <span className="muted">Card and bank transactions</span>
        <Link to="/import" className="chip chip-action">
          <Icon name="download" size={16} />
          Import
        </Link>
      </div>
      {PlaidSync && banks.length > 0 && (
        <Suspense fallback={null}>
          <PlaidSync banks={banks} onSynced={afterSync} />
        </Suspense>
      )}
    </>
  )

  if (state.status === 'loading') {
    return (
      <>
        {bar}
      <div aria-hidden="true">
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-row" />
      </div>
      </>
    )
  }
  if (state.status === 'error') {
    return (
      <>
        {bar}
        {state.messages.map((message) => (
          <p key={message} className="notice notice-error" role="alert">
            {message}
          </p>
        ))}
      </>
    )
  }

  const { transactions } = state
  return (
    <>
      {bar}
      {transactions.length === 0 ? (
        <div className="card empty">
          <Icon name="bank" size={40} />
          <p>{month || category ? 'No bank or card transactions match these filters.' : 'No bank or card transactions yet.'}</p>
          <Link to="/import" className="button button-secondary">
            Import a statement
          </Link>
        </div>
      ) : (
        <>
          <div className="summary-row">
            <p className="summary">
              <span className="summary-total">{spendingTotals(transactions) || formatMoney('0', 'CAD')}</span>
              <span className="muted">
                spent · {transactions.length} transaction{transactions.length === 1 ? '' : 's'}
              </span>
            </p>
          </div>
          {groupByMonth(transactions).map(([groupMonth, items]) => (
            <section key={groupMonth} className="month-group" aria-label={formatMonth(groupMonth)}>
              <h2 className="month-heading">
                <span>{formatMonth(groupMonth)}</span>
                <span>{spendingTotals(items)}</span>
              </h2>
              <ul className="receipts">
                {items.map((t) => {
                  const value = parseDecimal(t.amount) ?? 0n
                  // Money in (refund, payment received) shows as +amount; spending as the plain amount.
                  const refund = value < 0n
                  return (
                    <li
                      key={t.transaction_id}
                      className={`receipt-card bank-row${t.kind !== 'spending' ? ' bank-row-other' : ''}`}
                    >
                      <details>
                        <summary className="receipt-head">
                          <span className="receipt-avatar" aria-hidden="true">
                            {t.kind === 'spending' ? categoryIcon(categories, t.category) : t.kind === 'income' ? '💰' : '🔁'}
                          </span>
                          <span className="receipt-info">
                            <span className="receipt-store">
                              {t.merchant || t.description}
                              {t.pending && <span className="badge badge-muted">Pending</span>}
                              {t.receipt_id && t.receipt_id !== 'none' && <span className="badge badge-muted">🧾 Receipt</span>}
                            </span>
                            <span className="muted">
                              {formatDate(t.date)}
                              {accountLabel(t.account_id) && ` · ${accountLabel(t.account_id)}`}
                            </span>
                          </span>
                          <span className="bank-row-right">
                            <strong className="receipt-amount">
                              {refund ? '+' : ''}
                              {formatMoney(formatDecimal(refund ? -value : value), t.currency)}
                            </strong>
                            <span className="chip chip-small">
                              {t.kind === 'transfer'
                                ? 'Transfer'
                                : t.kind === 'income'
                                  ? 'Income'
                                  : t.subcategory || t.category || 'Uncategorized'}
                            </span>
                          </span>
                        </summary>
                        {t.kind === 'spending' && !refund ? (
                          <ReceiptMatch transaction={t} onChanged={reload} />
                        ) : (
                          <p className="receipt-match-status muted">
                            {t.kind === 'spending'
                              ? 'A refund: it lowers spending in its category.'
                              : 'Not spending: transfers, card payments, and income are left out of your spending.'}
                          </p>
                        )}
                        <BankCategoryEditor transaction={t} categories={categories} onChanged={reload} />
                      </details>
                    </li>
                  )
                })}
              </ul>
            </section>
          ))}
        </>
      )}
    </>
  )
}
