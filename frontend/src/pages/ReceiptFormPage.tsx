import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  createReceipt,
  deleteReceipt,
  errorMessages,
  getReceipt,
  updateReceipt,
  type ReceiptInput,
} from '../api/client'
import { useCategories } from '../hooks/useCategories'
import { formatDecimal, lineTotal, parseDecimal, sum } from '../lib/decimal'
import { formatMoney, todayIso } from '../lib/format'

const DEFAULT_CURRENCY = 'USD'
const COMMON_CURRENCIES = ['USD', 'CAD', 'JPY', 'EUR', 'GBP', 'AUD', 'CNY', 'KRW', 'MXN']

interface ItemRow {
  key: number
  itemId?: string
  name: string
  quantity: string
  price: string
  category: string
}

interface FormState {
  store: string
  purchasedAt: string
  currency: string
  total: string
  items: ItemRow[]
}

let nextKey = 1
const newItem = (): ItemRow => ({ key: nextKey++, name: '', quantity: '1', price: '', category: '' })

const emptyForm = (): FormState => ({
  store: '',
  purchasedAt: todayIso(),
  currency: DEFAULT_CURRENCY,
  total: '',
  items: [newItem()],
})

function itemsTotal(items: ItemRow[]): bigint | null {
  const lines = items.map((item) => lineTotal(item.quantity, item.price))
  return lines.every((line) => line !== null) ? sum(lines as bigint[]) : null
}

export function ReceiptFormPage() {
  const { receiptId } = useParams()
  const isEdit = receiptId !== undefined
  const navigate = useNavigate()
  const categories = useCategories()

  const [form, setForm] = useState<FormState>(emptyForm)
  // While false, the total follows the sum of the items. Typing in the total field stops that.
  const [totalEdited, setTotalEdited] = useState(false)
  const [loadError, setLoadError] = useState<string[] | null>(null)
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isEdit) return
    let active = true
    getReceipt(receiptId)
      .then((receipt) => {
        if (!active) return
        setForm({
          store: receipt.store,
          purchasedAt: receipt.purchased_at,
          currency: receipt.currency,
          total: receipt.total,
          items: receipt.items.map((item) => ({
            key: nextKey++,
            itemId: item.item_id,
            name: item.name,
            quantity: item.quantity,
            price: item.price,
            category: item.category,
          })),
        })
        setTotalEdited(true)
      })
      .catch((error: unknown) => {
        if (active) setLoadError(errorMessages(error))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [isEdit, receiptId])

  const computedTotal = useMemo(() => itemsTotal(form.items), [form.items])
  const total = totalEdited ? form.total : computedTotal === null ? form.total : formatDecimal(computedTotal)
  const enteredTotal = parseDecimal(total)
  const difference = computedTotal !== null && enteredTotal !== null ? enteredTotal - computedTotal : null

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const setItem = (key: number, changes: Partial<ItemRow>) =>
    setForm((current) => ({
      ...current,
      items: current.items.map((item) => (item.key === key ? { ...item, ...changes } : item)),
    }))

  const removeItem = (key: number) =>
    setForm((current) => ({ ...current, items: current.items.filter((item) => item.key !== key) }))

  const showErrors = (messages: string[]) => {
    setErrors(messages)
    requestAnimationFrame(() => errorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const input: ReceiptInput = {
      store: form.store,
      purchased_at: form.purchasedAt,
      currency: form.currency.trim().toUpperCase(),
      total,
      items: form.items.map((item) => ({
        ...(item.itemId ? { item_id: item.itemId } : {}),
        name: item.name,
        quantity: item.quantity,
        price: item.price,
        category: item.category,
      })),
    }
    setSaving(true)
    setErrors([])
    try {
      const saved = isEdit ? await updateReceipt(receiptId, input) : await createReceipt(input)
      navigate(`/transactions?month=${saved.purchased_at.slice(0, 7)}`)
    } catch (error) {
      showErrors(errorMessages(error))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!isEdit || !window.confirm('Delete this receipt? You can still find it in your spreadsheet.')) return
    setSaving(true)
    try {
      await deleteReceipt(receiptId)
      navigate(`/transactions?month=${form.purchasedAt.slice(0, 7)}`)
    } catch (error) {
      showErrors(errorMessages(error))
      setSaving(false)
    }
  }

  if (loading) {
    return <p className="muted center">Loading receipt…</p>
  }
  if (loadError) {
    return (
      <section className="page">
        {loadError.map((message) => (
          <p key={message} className="notice notice-error" role="alert">
            {message}
          </p>
        ))}
        <Link to="/transactions">← Back to transactions</Link>
      </section>
    )
  }

  const currency = form.currency.trim().toUpperCase() || DEFAULT_CURRENCY

  return (
    <section className="page">
      <div className="page-title">
        <h1>{isEdit ? 'Edit receipt' : 'Add a receipt'}</h1>
      </div>

      <form className="receipt-form" onSubmit={handleSubmit}>
        <div className="form-grid">
          <label className="span-2">
            Store
            <input
              value={form.store}
              onChange={(event) => setField('store', event.target.value)}
              placeholder="e.g. FreshMart"
              maxLength={200}
              required
            />
          </label>
          <label>
            Date
            <input
              type="date"
              value={form.purchasedAt}
              onChange={(event) => setField('purchasedAt', event.target.value)}
              required
            />
          </label>
          <label>
            Currency
            <input
              value={form.currency}
              onChange={(event) => setField('currency', event.target.value.toUpperCase())}
              list="currencies"
              maxLength={3}
              pattern="[A-Za-z]{3}"
              title="3-letter currency code, e.g. USD"
              required
            />
            <datalist id="currencies">
              {COMMON_CURRENCIES.map((code) => (
                <option key={code} value={code} />
              ))}
            </datalist>
          </label>
        </div>

        <fieldset className="items-editor">
          <legend>Items</legend>
          {form.items.map((item, index) => {
            const line = lineTotal(item.quantity, item.price)
            return (
              <div key={item.key} className="item-editor">
                <label className="item-field-name">
                  <span className="sr-only">Item {index + 1} name</span>
                  <input
                    value={item.name}
                    onChange={(event) => setItem(item.key, { name: event.target.value })}
                    placeholder="Item name"
                    maxLength={200}
                    required
                  />
                </label>
                <label className="item-field-qty">
                  <span className="field-hint">Qty</span>
                  <input
                    value={item.quantity}
                    onChange={(event) => setItem(item.key, { quantity: event.target.value })}
                    inputMode="decimal"
                    aria-label={`Item ${index + 1} quantity`}
                    required
                  />
                </label>
                <label className="item-field-price">
                  <span className="field-hint">Price</span>
                  <input
                    value={item.price}
                    onChange={(event) => setItem(item.key, { price: event.target.value })}
                    inputMode="decimal"
                    placeholder="0.00"
                    aria-label={`Item ${index + 1} unit price`}
                    required
                  />
                </label>
                <label className="item-field-category">
                  <span className="sr-only">Item {index + 1} category</span>
                  <select
                    value={item.category}
                    onChange={(event) => setItem(item.key, { category: event.target.value })}
                    required
                  >
                    <option value="" disabled>
                      Category…
                    </option>
                    {categories.status === 'loaded' &&
                      categories.categories.map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.icon} {c.name}
                        </option>
                      ))}
                    {item.category &&
                      categories.status === 'loaded' &&
                      !categories.categories.some((c) => c.name === item.category) && (
                        <option value={item.category}>{item.category}</option>
                      )}
                  </select>
                </label>
                <span className="item-line" aria-label={`Item ${index + 1} amount`}>
                  {line === null ? '—' : formatMoney(formatDecimal(line), currency)}
                </span>
                <button
                  type="button"
                  className="button button-ghost button-small"
                  onClick={() => removeItem(item.key)}
                  disabled={form.items.length === 1}
                  aria-label={`Remove item ${index + 1}`}
                >
                  ✕
                </button>
              </div>
            )
          })}
          <button
            type="button"
            className="button button-secondary button-small"
            onClick={() => setField('items', [...form.items, newItem()])}
          >
            + Add item
          </button>
          <p className="muted field-note">Enter the unit price; discounts can be items with a negative price.</p>
        </fieldset>

        <div className="total-row">
          <label>
            Total
            <input
              value={total}
              onChange={(event) => {
                setTotalEdited(true)
                setField('total', event.target.value)
              }}
              inputMode="decimal"
              required
            />
          </label>
          {computedTotal !== null && (
            <p className="muted">
              Items add up to {formatMoney(formatDecimal(computedTotal), currency)}
              {difference !== null && difference !== 0n && (
                <>
                  {' '}
                  ({difference > 0n ? '+' : ''}
                  {formatMoney(formatDecimal(difference), currency)} tax or other){' '}
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => {
                      setTotalEdited(false)
                      setField('total', '')
                    }}
                  >
                    Use items total
                  </button>
                </>
              )}
            </p>
          )}
        </div>

        <div ref={errorRef}>
          {errors.map((message) => (
            <p key={message} className="notice notice-error" role="alert">
              {message}
            </p>
          ))}
        </div>

        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={saving}>
            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Save receipt'}
          </button>
          <Link to="/transactions" className="button button-secondary">
            Cancel
          </Link>
          {isEdit && (
            <button type="button" className="button button-danger" onClick={handleDelete} disabled={saving}>
              Delete
            </button>
          )}
        </div>
      </form>
    </section>
  )
}
