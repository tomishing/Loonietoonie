import { useEffect, useState, type ChangeEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import {
  errorMessages,
  listAccounts,
  previewImport,
  readStatementFile,
  runImport,
  type AccountInfo,
  type ColumnMapping,
  type ImportPreview,
  type ImportResult,
  type PreviewRow,
  type StatementFile,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { AccountForm } from '../components/AccountForm'
import { Icon } from '../components/Icon'
import { formatDate, formatMoney } from '../lib/format'

// Rows of the file shown before importing.
const SHOWN_ROWS = 10
// Made-up transactions for trying the import without a real statement (public/).
const SAMPLE_FILE = 'sample-statement.csv'

const ORDERS: Record<ColumnMapping['date_order'], string> = {
  ymd: 'year-month-day',
  mdy: 'month/day/year',
  dmy: 'day/month/year',
}

/** Money out as an amount, money in as +amount (as in the Bank & card list). */
function signedMoney(amount: string | null, currency: string): string {
  if (amount === null) return '—'
  return amount.startsWith('-') ? `+${formatMoney(amount.slice(1), currency)}` : formatMoney(amount, currency)
}

function MappingSummary({ preview }: { preview: ImportPreview }) {
  const { mapping, columns } = preview
  if (!mapping) return <p className="muted">OFX/QFX file: the bank says what each value is.</p>
  const name = (index: number | null) => (index === null ? '' : `“${columns[index] ?? `Column ${index + 1}`}”`)
  return (
    <ul className="mapping-summary">
      <li>
        Date: {name(mapping.date)} ({ORDERS[mapping.date_order]})
      </li>
      <li>Description: {mapping.description.map(name).join(' + ')}</li>
      {mapping.amount !== null ? (
        <li>
          Amount: {name(mapping.amount)}, purchases are {mapping.purchases}
        </li>
      ) : (
        <li>
          {mapping.debit !== null && <>Money out: {name(mapping.debit)}</>}
          {mapping.debit !== null && mapping.credit !== null && ' · '}
          {mapping.credit !== null && <>Money in: {name(mapping.credit)}</>}
        </li>
      )}
    </ul>
  )
}

function ColumnSelect({
  label,
  value,
  preview,
  onChange,
}: {
  label: string
  value: number | null
  preview: ImportPreview
  onChange: (value: number) => void
}) {
  return (
    <label>
      {label}
      <select value={value ?? ''} onChange={(event) => onChange(Number(event.target.value))}>
        <option value="" disabled>
          Choose…
        </option>
        {preview.columns.map((column, index) => (
          <option key={index} value={index}>
            {column}
            {preview.examples[index] ? ` (e.g. ${preview.examples[index]})` : ''}
          </option>
        ))}
      </select>
    </label>
  )
}

/** Choose the columns by hand when detection is unsure or wrong. */
function MappingEditor({
  preview,
  onApply,
  onCancel,
}: {
  preview: ImportPreview
  onApply: (mapping: ColumnMapping) => void
  onCancel: () => void
}) {
  const start = preview.mapping as ColumnMapping
  const [mapping, setMapping] = useState<ColumnMapping>(start)
  const [mode, setMode] = useState<'signed' | 'split'>(start.amount !== null ? 'signed' : 'split')
  const set = (changes: Partial<ColumnMapping>) => setMapping({ ...mapping, ...changes })

  const apply = () =>
    onApply(
      mode === 'signed'
        ? { ...mapping, amount: mapping.amount ?? mapping.debit ?? 0, debit: null, credit: null }
        : { ...mapping, amount: null },
    )

  return (
    <div className="mapping-editor">
      <ColumnSelect label="Date" value={mapping.date} preview={preview} onChange={(date) => set({ date })} />
      <label>
        Date order
        <select
          value={mapping.date_order}
          onChange={(event) => set({ date_order: event.target.value as ColumnMapping['date_order'] })}
        >
          {Object.entries(ORDERS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <ColumnSelect
        label="Description"
        value={mapping.description[0] ?? null}
        preview={preview}
        onChange={(column) => set({ description: [column] })}
      />
      <label>
        Amounts are in
        <select value={mode} onChange={(event) => setMode(event.target.value as 'signed' | 'split')}>
          <option value="signed">One column (money in and out)</option>
          <option value="split">Two columns (money out, money in)</option>
        </select>
      </label>
      {mode === 'signed' ? (
        <>
          <ColumnSelect
            label="Amount"
            value={mapping.amount}
            preview={preview}
            onChange={(amount) => set({ amount })}
          />
          <label>
            Purchases show as
            <select
              value={mapping.purchases}
              onChange={(event) => set({ purchases: event.target.value as ColumnMapping['purchases'] })}
            >
              <option value="negative">Negative (−12.34)</option>
              <option value="positive">Positive (12.34)</option>
            </select>
          </label>
        </>
      ) : (
        <>
          <ColumnSelect label="Money out" value={mapping.debit} preview={preview} onChange={(debit) => set({ debit })} />
          <ColumnSelect
            label="Money in"
            value={mapping.credit}
            preview={preview}
            onChange={(credit) => set({ credit })}
          />
        </>
      )}
      <div className="household-form-actions">
        <button type="button" className="button button-primary" onClick={apply}>
          Apply
        </button>
        <button type="button" className="button button-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function RowList({ rows, currency }: { rows: PreviewRow[]; currency: string }) {
  return (
    <ul className="import-rows">
      {rows.map((row) => (
        <li key={row.line}>
          <span className="import-row-date">{row.date ? formatDate(row.date) : '—'}</span>
          <span className="import-row-text">{row.description}</span>
          <span className={row.amount?.startsWith('-') ? 'import-row-amount money-in' : 'import-row-amount'}>
            {signedMoney(row.amount, currency)}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Import a statement file: pick the file and the account, check the preview, import. */
export function ImportPage() {
  const { user } = useAuth()
  const [searchParams] = useSearchParams()
  const [accounts, setAccounts] = useState<AccountInfo[] | null>(null)
  const [accountId, setAccountId] = useState(searchParams.get('account') ?? '')
  const [adding, setAdding] = useState(false)
  const [file, setFile] = useState<StatementFile | null>(null)
  // Bumped for every file chosen (the same file again is read again).
  const [fileVersion, setFileVersion] = useState(0)
  // A mapping the user chose in the editor; null to let the server detect (or reuse the saved one).
  const [mapping, setMapping] = useState<ColumnMapping | null>(null)
  // The preview remembers which file, account, and mapping it belongs to; others mean "reading".
  const [previewState, setPreviewState] = useState<{
    key: string
    preview: ImportPreview | null
    error: string[] | null
  } | null>(null)
  const [editingMapping, setEditingMapping] = useState(false)
  // Possible duplicates the user ticked to import anyway.
  const [included, setIncluded] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string[] | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)

  useEffect(() => {
    listAccounts()
      .then((all) => {
        const usable = all.filter((info) => !info.linked)
        setAccounts(usable)
        // Start with the account used last (or the only one).
        setAccountId((current) => {
          if (current && usable.some((info) => info.account.account_id === current)) return current
          const latest = [...usable].sort((a, b) =>
            (b.last_import?.created_at ?? '').localeCompare(a.last_import?.created_at ?? ''),
          )[0]
          return latest?.account.account_id ?? ''
        })
      })
      .catch((failure: unknown) => setError(errorMessages(failure)))
  }, [])

  const account = accounts?.find((info) => info.account.account_id === accountId)?.account
  const currency = account?.currency ?? user?.default_currency ?? 'CAD'

  const previewKey = file && accountId ? `${fileVersion}|${accountId}|${JSON.stringify(mapping)}` : ''
  const current = previewState?.key === previewKey ? previewState : null
  const preview = current?.preview ?? null
  const reading = previewKey !== '' && current === null

  useEffect(() => {
    if (!file || !accountId) return
    let active = true
    const key = `${fileVersion}|${accountId}|${JSON.stringify(mapping)}`
    previewImport(accountId, file, mapping)
      .then((result) => {
        if (!active) return
        setPreviewState({ key, preview: result, error: null })
        setIncluded(new Set())
        setEditingMapping(!result.mapping_confident && result.mapping !== null)
      })
      .catch((failure: unknown) => {
        if (active) setPreviewState({ key, preview: null, error: errorMessages(failure) })
      })
    return () => {
      active = false
    }
  }, [file, fileVersion, accountId, mapping])

  const chooseFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0]
    event.target.value = '' // choosing the same file again still triggers a change
    if (!chosen) return
    setResult(null)
    setMapping(null)
    setError(null)
    try {
      setFile(await readStatementFile(chosen))
      setFileVersion((v) => v + 1)
    } catch {
      setError(["Couldn't read that file. Please choose it again."])
    }
  }

  const loadSample = async () => {
    setResult(null)
    setMapping(null)
    setError(null)
    try {
      const response = await fetch(`${import.meta.env.BASE_URL}${SAMPLE_FILE}`)
      const blob = await response.blob()
      setFile(await readStatementFile(new File([blob], SAMPLE_FILE, { type: 'text/csv' })))
      setFileVersion((v) => v + 1)
    } catch {
      setError(["Couldn't load the sample file."])
    }
  }

  const startImport = async () => {
    if (!file || !accountId) return
    setBusy('Importing and sorting into categories…')
    setError(null)
    try {
      setResult(await runImport(accountId, file, mapping, [...included]))
      setFile(null)
      setPreviewState(null)
      setMapping(null)
    } catch (failure) {
      setError(errorMessages(failure))
    } finally {
      setBusy(null)
    }
  }

  const toImport = preview ? preview.counts.new + included.size : 0
  const newRows = preview?.rows.filter((row) => row.status === 'new') ?? []
  const lookalikes = preview?.rows.filter((row) => row.status === 'possible_duplicate') ?? []
  const skipped = preview?.rows.filter((row) => row.status === 'skipped') ?? []

  return (
    <section className="page page-stack">
      <div className="page-title">
        <h1>Import a statement</h1>
      </div>

      {result && (
        <div className="card import-done">
          <p className="notice notice-ok" role="status">
            Imported {result.added} transaction{result.added === 1 ? '' : 's'}
            {result.duplicates ? ` (${result.duplicates} were already there)` : ''}.
          </p>
          <div className="household-form-actions">
            <Link to="/transactions?view=bank" className="button button-primary">
              See transactions
            </Link>
          </div>
        </div>
      )}

      <section className="card import-step" aria-labelledby="import-account-title">
        <h2 id="import-account-title" className="card-title">
          Account
        </h2>
        {accounts !== null && accounts.length > 0 && !adding && (
          <label>
            Import into
            <select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
              <option value="" disabled>
                Choose…
              </option>
              {accounts.map(({ account: a }) => (
                <option key={a.account_id} value={a.account_id}>
                  {a.institution} {a.name}
                  {a.mask ? ` •• ${a.mask}` : ''}
                </option>
              ))}
            </select>
          </label>
        )}
        {adding || accounts?.length === 0 ? (
          <AccountForm
            defaultCurrency={user?.default_currency ?? 'CAD'}
            onSaved={(created) => {
              setAccounts([...(accounts ?? []), { account: created, linked: false, last_import: null }])
              setAccountId(created.account_id)
              setAdding(false)
            }}
            onCancel={() => setAdding(false)}
          />
        ) : (
          <button type="button" className="button button-ghost button-small" onClick={() => setAdding(true)}>
            <Icon name="plus" size={16} />
            Add an account or card
          </button>
        )}
      </section>

      <section className="card import-step" aria-labelledby="import-file-title">
        <h2 id="import-file-title" className="card-title">
          Statement file
        </h2>
        <p className="muted">
          On your bank's website, download your transactions as <strong>CSV</strong> (spreadsheet), or as{' '}
          <strong>OFX/QFX</strong> (Quicken or Microsoft Money).
        </p>
        <label className="button button-secondary file-button">
          <Icon name="download" size={20} />
          {file ? `Chosen: ${file.filename}` : 'Choose a file'}
          <input
            type="file"
            accept=".csv,.ofx,.qfx,text/csv,text/plain,application/x-ofx,application/vnd.intu.qfx"
            onChange={(event) => void chooseFile(event)}
            className="sr-only"
          />
        </label>
        <button type="button" className="button button-ghost button-small" onClick={() => void loadSample()}>
          No statement handy? Try a sample file
        </button>
      </section>

      {(busy || reading) && (
        <p className="muted" role="status">
          {busy ?? 'Reading the file…'}
        </p>
      )}
      {[...(error ?? []), ...(current?.error ?? [])].map((message) => (
        <p key={message} className="notice notice-error" role="alert">
          {message}
        </p>
      ))}

      {preview && file && (
        <section className="card import-step" aria-labelledby="import-preview-title">
          <div className="card-head">
            <h2 id="import-preview-title" className="card-title">
              Check before importing
            </h2>
            {preview.mapping && !editingMapping && (
              <button type="button" className="chip chip-action" onClick={() => setEditingMapping(true)}>
                <Icon name="pencil" size={16} />
                Change
              </button>
            )}
          </div>
          {!preview.mapping_confident && (
            <p className="notice notice-warn">Please check how the file was read: some columns were a guess.</p>
          )}
          {editingMapping ? (
            <MappingEditor
              preview={preview}
              onApply={(chosen) => {
                setEditingMapping(false)
                setMapping(chosen)
              }}
              onCancel={() => setEditingMapping(false)}
            />
          ) : (
            <MappingSummary preview={preview} />
          )}

          <p className="import-counts">
            <strong>{preview.counts.new} new</strong>
            {preview.counts.duplicate > 0 && ` · ${preview.counts.duplicate} already imported`}
            {preview.counts.possible_duplicate > 0 && ` · ${preview.counts.possible_duplicate} to check`}
            {preview.counts.skipped > 0 && ` · ${preview.counts.skipped} skipped`}
            {preview.date_from && preview.date_to && (
              <span className="muted">
                {' '}
                ({formatDate(preview.date_from)} – {formatDate(preview.date_to)})
              </span>
            )}
          </p>

          {newRows.length > 0 && (
            <>
              <RowList rows={newRows.slice(0, SHOWN_ROWS)} currency={currency} />
              {newRows.length > SHOWN_ROWS && (
                <p className="muted">…and {newRows.length - SHOWN_ROWS} more.</p>
              )}
            </>
          )}

          {lookalikes.length > 0 && (
            <fieldset className="import-lookalikes">
              <legend>Possibly already imported. Tick the ones to import anyway:</legend>
              {lookalikes.map((row) => (
                <label key={row.line} className="check-row">
                  <input
                    type="checkbox"
                    checked={included.has(row.line)}
                    onChange={(event) => {
                      const next = new Set(included)
                      if (event.target.checked) next.add(row.line)
                      else next.delete(row.line)
                      setIncluded(next)
                    }}
                  />
                  <span>
                    {row.date ? formatDate(row.date) : ''} {row.description} {signedMoney(row.amount, currency)}
                    <span className="muted"> · {row.reason}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}

          {skipped.length > 0 && (
            <details>
              <summary className="muted">
                {skipped.length} row{skipped.length === 1 ? '' : 's'} can't be imported
              </summary>
              <ul className="import-skipped">
                {skipped.map((row) => (
                  <li key={row.line}>
                    Line {row.line}: {row.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <p className="muted import-privacy">
            New merchants' names (not amounts or dates) are sent to Claude to suggest categories. You can change any
            category later.
          </p>
          <button
            type="button"
            className="button button-primary button-block"
            onClick={() => void startImport()}
            disabled={busy !== null || reading || editingMapping || toImport === 0}
          >
            {toImport === 0 ? 'Nothing new to import' : `Import ${toImport} transaction${toImport === 1 ? '' : 's'}`}
          </button>
        </section>
      )}
    </section>
  )
}
