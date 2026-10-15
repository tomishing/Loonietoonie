import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { errorMessages, listBankTransactions, syncBanks, type BankAccount, type BankTransaction } from '../api/client'
import { categoryIcon, type CategoriesState } from '../hooks/useCategories'
import { formatDecimal, parseDecimal } from '../lib/decimal'
import { formatDate, formatMoney, formatMonth, monthRange } from '../lib/format'
import { Icon } from './Icon'
import { ReceiptMatch } from './ReceiptMatch'

type State = { status: 'loading' } | { status: 'loaded'; transactions: BankTransaction[] } | { status: 'error'; messages: string[] }

// Banks are synced once per app session when this list first opens; "Sync now" on Account forces it.
let syncedThisSession = false

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
}: {
  month: string
  category: string
  accounts: BankAccount[]
  categories: CategoriesState
}) {
  // Bumped after a match changes, to reload the list.
  const [version, setVersion] = useState(0)
  const reload = () => setVersion((v) => v + 1)
  // The result remembers which filters it belongs to; different filters mean "loading".
  const key = `${month}|${category}`
  const [result, setResult] = useState<{ key: string; state: State } | null>(null)
  const state: State = result?.key === key ? result.state : { status: 'loading' }
  const [syncing, setSyncing] = useState(!syncedThisSession)

  useEffect(() => {
    let active = true
    const range = month ? monthRange(month) : null
    const load = () =>
      listBankTransactions({ from: range?.from, to: range?.to, category: category || undefined })
        .then((transactions) => active && setResult({ key, state: { status: 'loaded', transactions } }))
        .catch(
          (error: unknown) => active && setResult({ key, state: { status: 'error', messages: errorMessages(error) } }),
        )
    if (syncedThisSession) {
      void load()
    } else {
      // Show what's already imported, then refresh after syncing with the banks.
      void load()
        .then(() => syncBanks())
        .then(() => {
          syncedThisSession = true
          return active ? load() : undefined
        })
        .catch(() => undefined) // a failed sync still leaves the imported list on screen
        .finally(() => active && setSyncing(false))
    }
    return () => {
      active = false
    }
  }, [month, category, key, version]) // a reload after a match change keeps the list on screen

  const accountLabel = (id: string) => {
    const account = accounts.find((a) => a.account_id === id)
    if (!account) return ''
    return account.mask ? `${account.name} •• ${account.mask}` : account.name
  }

  if (state.status === 'loading') {
    return (
      <div aria-hidden="true">
        <div className="skeleton skeleton-row" />
        <div className="skeleton skeleton-row" />
      </div>
    )
  }
  if (state.status === 'error') {
    return (
      <>
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
      {syncing && (
        <p className="muted sync-note" role="status">
          Checking your banks for new transactions…
        </p>
      )}
      {transactions.length === 0 ? (
        <div className="card empty">
          <Icon name="bank" size={40} />
          <p>{month || category ? 'No bank or card transactions match these filters.' : 'No bank or card transactions yet.'}</p>
          <Link to="/account" className="button button-secondary">
            Manage linked banks
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
