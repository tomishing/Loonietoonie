import { useState } from 'react'
import { errorMessages, setBankCategory, type BankTransaction } from '../api/client'
import type { CategoriesState } from '../hooks/useCategories'

const TRANSFER = '(transfer)'
const INCOME = '(income)'

/** Under a bank charge: change its category, for this charge or every charge from the merchant. */
export function BankCategoryEditor({
  transaction,
  categories,
  onChanged,
}: {
  transaction: BankTransaction
  categories: CategoriesState
  onChanged: () => void
}) {
  const current =
    transaction.kind === 'transfer' ? TRANSFER : transaction.kind === 'income' ? INCOME : transaction.category
  const [open, setOpen] = useState(false)
  const [category, setCategory] = useState(current)
  const [subcategory, setSubcategory] = useState(transaction.subcategory)
  const [allFromMerchant, setAllFromMerchant] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const merchant = transaction.merchant || transaction.description

  const list = categories.status === 'loaded' ? categories.categories : []
  const subcategories = list.find((c) => c.name === category)?.subcategories ?? []

  const save = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const kind = category === TRANSFER ? 'transfer' : category === INCOME ? 'income' : 'spending'
      const changed = await setBankCategory(transaction.transaction_id, {
        kind,
        category: kind === 'spending' ? category : '',
        subcategory: kind === 'spending' ? subcategory : '',
        applyToMerchant: allFromMerchant,
      })
      setMessage({
        tone: 'ok',
        text: allFromMerchant
          ? `Changed ${changed} charge${changed === 1 ? '' : 's'} from ${merchant}; new ones will follow.`
          : 'Changed this charge.',
      })
      setOpen(false)
      onChanged()
    } catch (error) {
      setMessage({ tone: 'error', text: errorMessages(error).join(' ') })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="category-editor">
      {!open ? (
        <div className="receipt-actions">
          <button type="button" className="chip chip-action" onClick={() => setOpen(true)}>
            Change category
          </button>
        </div>
      ) : (
        <div className="category-editor-form">
          <label>
            <span className="sr-only">Category</span>
            <select
              value={category}
              onChange={(event) => {
                setCategory(event.target.value)
                setSubcategory('')
              }}
            >
              {list.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.icon} {c.name}
                </option>
              ))}
              <option value={TRANSFER}>🔁 Not spending (transfer or card payment)</option>
              <option value={INCOME}>💰 Not spending (income)</option>
            </select>
          </label>
          {subcategories.length > 0 && (
            <label>
              <span className="sr-only">Subcategory</span>
              <select value={subcategory} onChange={(event) => setSubcategory(event.target.value)}>
                <option value="">Subcategory (optional)</option>
                {subcategories.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="checkbox-row">
            <input type="checkbox" checked={allFromMerchant} onChange={(event) => setAllFromMerchant(event.target.checked)} />
            <span>All charges from {merchant}, including new ones</span>
          </label>
          <div className="receipt-actions">
            <button type="button" className="button button-primary button-small" onClick={() => void save()} disabled={busy}>
              {busy ? 'Saving…' : 'Save'}
            </button>
            <button type="button" className="button button-ghost button-small" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {message && (
        <p className={`notice notice-${message.tone}`} role={message.tone === 'error' ? 'alert' : 'status'}>
          {message.text}
        </p>
      )}
    </div>
  )
}
