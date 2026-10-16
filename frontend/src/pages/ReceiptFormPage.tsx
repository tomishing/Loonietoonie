import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import {
  createReceipt,
  deleteReceipt,
  duplicateOf,
  errorMessages,
  getReceipt,
  updateReceipt,
  uploadReceiptImage,
  type DuplicateReceipt,
  type ReceiptInput,
  type ScanIssue,
  type ScanResult,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useCategories } from '../hooks/useCategories'
import { formatDecimal, lineTotal, parseDecimal, sum } from '../lib/decimal'
import { formatDate, formatMoney, todayIso } from '../lib/format'
import type { ScanReviewState } from './ScanPage'
import type { TransactionsLocationState } from './TransactionsPage'

const FALLBACK_CURRENCY = 'CAD'
const COMMON_CURRENCIES = ['CAD', 'USD', 'JPY', 'EUR', 'GBP', 'AUD', 'CNY', 'KRW', 'MXN']
const CURRENCY_NAMES: Record<string, string> = {
  CAD: 'Canadian dollar',
  USD: 'US dollar',
  JPY: 'Japanese yen',
  EUR: 'Euro',
  GBP: 'British pound',
  AUD: 'Australian dollar',
  CNY: 'Chinese yuan',
  KRW: 'Korean won',
  MXN: 'Mexican peso',
}
const OTHER_CURRENCY = 'other'

interface ItemRow {
  key: number
  itemId?: string
  name: string
  quantity: string
  price: string
  category: string
  /** "" for none. */
  subcategory: string
}

interface FormState {
  store: string
  purchasedAt: string
  currency: string
  total: string
  items: ItemRow[]
}

let nextKey = 1
const newItem = (): ItemRow => ({
  key: nextKey++,
  name: '',
  quantity: '1',
  price: '',
  category: '',
  subcategory: '',
})

const emptyForm = (currency: string): FormState => ({
  store: '',
  purchasedAt: todayIso(),
  currency,
  total: '',
  items: [newItem()],
})

/** Form pre-filled from a scan. Unreadable values stay empty for the user to fill in. */
const formFromScan = ({ draft }: ScanResult): FormState => ({
  store: draft.store,
  purchasedAt: draft.purchased_at ?? todayIso(),
  currency: draft.currency,
  total: draft.total ?? '',
  items: draft.items.length
    ? draft.items.map((item) => ({
        key: nextKey++,
        name: item.name,
        quantity: item.quantity ?? '1',
        price: item.price ?? '',
        category: item.category,
        subcategory: item.subcategory ?? '',
      }))
    : [newItem()],
})

function isScanReview(state: unknown): state is ScanReviewState {
  return typeof state === 'object' && state !== null && 'scan' in state && 'image' in state
}

function itemsTotal(items: ItemRow[]): bigint | null {
  const lines = items.map((item) => lineTotal(item.quantity, item.price))
  return lines.every((line) => line !== null) ? sum(lines as bigint[]) : null
}

export function ReceiptFormPage() {
  const { receiptId } = useParams()
  const isEdit = receiptId !== undefined
  const navigate = useNavigate()
  const location = useLocation()
  const categories = useCategories()
  const { user } = useAuth()
  const defaultCurrency = user?.default_currency ?? FALLBACK_CURRENCY

  // A scan arrives through router state from the Scan page.
  const [review] = useState<ScanReviewState | null>(() =>
    !isEdit && isScanReview(location.state) ? location.state : null,
  )
  const [form, setForm] = useState<FormState>(() => (review ? formFromScan(review.scan) : emptyForm(defaultCurrency)))
  // While false, the total follows the sum of the items. Typing in the total field stops that.
  // A scanned total is kept as read, so a mismatch with the items stays visible.
  const [totalEdited, setTotalEdited] = useState(review !== null)
  const [photoUrl] = useState(() => (review ? URL.createObjectURL(review.image) : null))
  const [loadError, setLoadError] = useState<string[] | null>(null)
  const [loading, setLoading] = useState(isEdit)
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  // A saved receipt that looks like this one (the server asked to confirm before saving twice).
  const [duplicate, setDuplicate] = useState<DuplicateReceipt | null>(null)
  // "Other…" in the currency dropdown switches to a text box for any 3-letter code.
  const [customCurrency, setCustomCurrency] = useState(false)
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => () => (photoUrl ? URL.revokeObjectURL(photoUrl) : undefined), [photoUrl])

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
            subcategory: item.subcategory,
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

  // A new category keeps the subcategory only if the new category has one with that name.
  const setCategory = (key: number, category: string) =>
    setForm((current) => ({
      ...current,
      items: current.items.map((item) =>
        item.key === key
          ? {
              ...item,
              category,
              subcategory: subcategoriesOf(category).includes(item.subcategory) ? item.subcategory : '',
            }
          : item,
      ),
    }))

  const subcategoriesOf = (category: string): string[] =>
    categories.status === 'loaded' ? (categories.categories.find((c) => c.name === category)?.subcategories ?? []) : []

  const removeItem = (key: number) =>
    setForm((current) => ({ ...current, items: current.items.filter((item) => item.key !== key) }))

  const showErrors = (messages: string[]) => {
    setErrors(messages)
    requestAnimationFrame(() => errorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }

  const handleSubmit = async (event: FormEvent, allowDuplicate = false) => {
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
        subcategory: item.subcategory || null,
      })),
    }
    setSaving(true)
    setErrors([])
    setDuplicate(null)
    try {
      const saved = isEdit ? await updateReceipt(receiptId, input) : await createReceipt(input, allowDuplicate)
      let notice: string | undefined
      if (review) {
        try {
          await uploadReceiptImage(saved.receipt_id, review.image)
        } catch {
          notice = "The receipt was saved, but its photo couldn't be uploaded to Google Drive."
        }
      }
      const state: TransactionsLocationState = { highlight: saved.receipt_id, notice }
      navigate('/transactions', { state })
    } catch (error) {
      const saved = duplicateOf(error)
      if (saved) {
        setDuplicate(saved)
        requestAnimationFrame(() => errorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
      } else {
        showErrors(errorMessages(error))
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!isEdit || !window.confirm('Delete this receipt? You can still find it in your spreadsheet.')) return
    setSaving(true)
    try {
      await deleteReceipt(receiptId)
      navigate('/transactions')
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

  const currency = form.currency.trim().toUpperCase() || defaultCurrency
  const looksDuplicate =
    duplicate !== null || (review?.scan.issues.some((issue) => issue.code === 'possible_duplicate') ?? false)

  return (
    <section className="page">
      <div className="page-title">
        <h1>{isEdit ? 'Edit receipt' : review ? 'Check the receipt' : 'Add a receipt'}</h1>
      </div>

      {review && <ScanSummary issues={review.scan.issues} photoUrl={photoUrl} />}

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
            {/* A dropdown, not a text box with suggestions: Android only suggests codes that start
                with what's already typed, so with "CAD" filled in no other currency showed up. */}
            {customCurrency || !COMMON_CURRENCIES.includes(form.currency) ? (
              <input
                value={form.currency}
                onChange={(event) => setField('currency', event.target.value.toUpperCase())}
                maxLength={3}
                pattern="[A-Za-z]{3}"
                title="3-letter currency code, e.g. CHF"
                placeholder="e.g. CHF"
                autoCapitalize="characters"
                required
              />
            ) : (
              <select
                value={form.currency}
                onChange={(event) => {
                  if (event.target.value === OTHER_CURRENCY) {
                    setCustomCurrency(true)
                    setField('currency', '')
                  } else {
                    setField('currency', event.target.value)
                  }
                }}
                required
              >
                {COMMON_CURRENCIES.map((code) => (
                  <option key={code} value={code}>
                    {code} · {CURRENCY_NAMES[code]}
                  </option>
                ))}
                <option value={OTHER_CURRENCY}>Other…</option>
              </select>
            )}
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
                    onChange={(event) => setCategory(item.key, event.target.value)}
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
                <SubcategorySelect
                  label={`Item ${index + 1} subcategory`}
                  value={item.subcategory}
                  options={subcategoriesOf(item.category)}
                  disabled={!item.category}
                  onChange={(subcategory) => setItem(item.key, { subcategory })}
                />
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
          {duplicate && (
            <div className="notice notice-warn duplicate-warning" role="alert">
              <p>
                <strong>Already saved?</strong> This looks like {duplicate.store} on {formatDate(duplicate.purchased_at)},{' '}
                {formatMoney(duplicate.total, duplicate.currency)}, which is already in your receipts.
              </p>
              <div className="receipt-actions">
                <Link to={`/receipts/${duplicate.receipt_id}/edit`} className="chip chip-action">
                  View saved receipt
                </Link>
                <button
                  type="button"
                  className="chip chip-action"
                  disabled={saving}
                  onClick={(event) => void handleSubmit(event, true)}
                >
                  Save anyway
                </button>
              </div>
            </div>
          )}
          {errors.map((message) => (
            <p key={message} className="notice notice-error" role="alert">
              {message}
            </p>
          ))}
        </div>

        <div className="form-actions">
          {/* While a duplicate warning shows (from the scan, or "Already saved?" after Save), Cancel is
              the highlighted button, so saving twice takes a deliberate choice. */}
          <button
            type="submit"
            className={`button ${looksDuplicate ? 'button-secondary' : 'button-primary'}`}
            disabled={saving}
          >
            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Save receipt'}
          </button>
          <Link to="/transactions" className={`button ${looksDuplicate ? 'button-primary' : 'button-secondary'}`}>
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

function ScanSummary({ issues, photoUrl }: { issues: ScanIssue[]; photoUrl: string | null }) {
  return (
    <div className="scan-summary">
      {photoUrl && (
        <a href={photoUrl} target="_blank" rel="noreferrer" className="scan-thumb">
          <img src={photoUrl} alt="Scanned receipt" />
        </a>
      )}
      <div>
        {issues.length === 0 ? (
          <p className="notice notice-ok">Everything adds up. Check the details, then save.</p>
        ) : (
          <div className="notice notice-warn" role="status">
            <p>Please check these before saving:</p>
            <ul>
              {issues.map((issue) => (
                <li key={issue.code + issue.message}>{issue.message}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

function SubcategorySelect({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string
  value: string
  options: string[]
  disabled: boolean
  onChange: (value: string) => void
}) {
  // A saved subcategory that was later removed from the list stays selectable.
  const all = value && !options.includes(value) ? [...options, value] : options
  return (
    <label className="item-field-subcategory">
      <span className="sr-only">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled || all.length === 0}>
        <option value="">{all.length === 0 && !disabled ? 'No subcategories' : 'Subcategory (optional)'}</option>
        {all.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </label>
  )
}
