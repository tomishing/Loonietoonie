import { useState, type FormEvent } from 'react'
import { createAccount, errorMessages, updateAccount, type AccountDraft, type BankAccount } from '../api/client'

const CURRENCIES = ['CAD', 'USD', 'EUR', 'GBP', 'JPY']

/** Add or edit an account that statement files are imported into. */
export function AccountForm({
  account,
  defaultCurrency,
  onSaved,
  onCancel,
}: {
  /** The account to edit; omitted to add one. */
  account?: BankAccount
  defaultCurrency: string
  onSaved: (account: BankAccount) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<AccountDraft>({
    institution: account?.institution ?? '',
    name: account?.name ?? '',
    type: account?.type === 'depository' ? 'depository' : 'credit',
    mask: account?.mask ?? '',
    currency: account?.currency ?? defaultCurrency,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string[] | null>(null)
  const currencies = CURRENCIES.includes(draft.currency) ? CURRENCIES : [draft.currency, ...CURRENCIES]

  const set = <K extends keyof AccountDraft>(key: K, value: AccountDraft[K]) => setDraft({ ...draft, [key]: value })

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onSaved(account ? await updateAccount(account.account_id, draft) : await createAccount(draft))
    } catch (failure) {
      setError(errorMessages(failure))
      setBusy(false)
    }
  }

  return (
    <form className="account-form" onSubmit={(event) => void submit(event)}>
      <label>
        Bank
        <input
          value={draft.institution}
          onChange={(event) => set('institution', event.target.value)}
          placeholder="CIBC"
          maxLength={100}
          required
        />
      </label>
      <label>
        Account name
        <input
          value={draft.name}
          onChange={(event) => set('name', event.target.value)}
          placeholder="Aventura Visa"
          maxLength={100}
          required
        />
      </label>
      <div className="account-form-row">
        <label>
          Type
          <select value={draft.type} onChange={(event) => set('type', event.target.value as AccountDraft['type'])}>
            <option value="credit">Credit card</option>
            <option value="depository">Bank account</option>
          </select>
        </label>
        <label>
          Last 4 digits
          <input
            value={draft.mask}
            onChange={(event) => set('mask', event.target.value.replace(/\D/g, '').slice(0, 4))}
            inputMode="numeric"
            placeholder="Optional"
          />
        </label>
        <label>
          Currency
          <select value={draft.currency} onChange={(event) => set('currency', event.target.value)}>
            {currencies.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <p className="notice notice-error" role="alert">
          {error.join(' ')}
        </p>
      )}
      <div className="household-form-actions">
        <button type="submit" className="button button-primary" disabled={busy}>
          {busy ? 'Saving…' : account ? 'Save' : 'Add account'}
        </button>
        <button type="button" className="button button-ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  )
}
