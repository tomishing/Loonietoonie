import { useState } from 'react'
import { Link } from 'react-router'
import { chooseReceipt, errorMessages, receiptCandidates, type BankTransaction, type ReceiptCandidate } from '../api/client'
import { formatDate, formatMoney } from '../lib/format'
import { Icon } from './Icon'

/** Under a card/bank charge: its matched receipt, or a way to pick one or say there is none. */
export function ReceiptMatch({ transaction, onChanged }: { transaction: BankTransaction; onChanged: () => void }) {
  const [options, setOptions] = useState<ReceiptCandidate[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const matched = transaction.receipt_id && transaction.receipt_id !== 'none'

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChanged()
    } catch (reason) {
      setError(errorMessages(reason).join(' '))
    } finally {
      setBusy(false)
    }
  }

  const loadOptions = () =>
    void run(async () => {
      setOptions(await receiptCandidates(transaction.transaction_id))
    })

  return (
    <div className="receipt-match">
      {matched ? (
        <>
          <p className="receipt-match-status">
            <Icon name="sheet" size={16} />
            Counted with its receipt, so it isn't added twice.
          </p>
          <div className="receipt-actions">
            <Link to={`/receipts/${transaction.receipt_id}/edit`} className="chip chip-action">
              View receipt
            </Link>
            <button
              type="button"
              className="chip chip-action"
              disabled={busy}
              onClick={() => void run(() => chooseReceipt(transaction.transaction_id, null))}
            >
              Not this receipt
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="receipt-match-status muted">
            {transaction.receipt_id === 'none'
              ? 'Marked as having no receipt.'
              : 'No receipt matched yet; it counts as one line in its category.'}
          </p>
          {options === null ? (
            <div className="receipt-actions">
              <button type="button" className="chip chip-action" disabled={busy} onClick={loadOptions}>
                Match a receipt
              </button>
              {transaction.receipt_id !== 'none' && (
                <button
                  type="button"
                  className="chip chip-action"
                  disabled={busy}
                  onClick={() => void run(() => chooseReceipt(transaction.transaction_id, null))}
                >
                  No receipt
                </button>
              )}
            </div>
          ) : options.length === 0 ? (
            <p className="muted">No receipts in {transaction.currency} within a week of this charge.</p>
          ) : (
            <ul className="match-options">
              {options.map((option) => (
                <li key={option.receipt_id}>
                  <button
                    type="button"
                    className="match-option"
                    disabled={busy}
                    onClick={() => void run(() => chooseReceipt(transaction.transaction_id, option.receipt_id))}
                  >
                    <span>
                      <strong>{option.store}</strong>
                      <span className="muted">{formatDate(option.purchased_at)}</span>
                    </span>
                    <span>
                      {formatMoney(option.total, option.currency)}
                      {option.likely && <span className="badge badge-muted">Likely</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
