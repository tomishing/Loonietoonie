import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import {
  errorMessages,
  listAccounts,
  listImports,
  undoImport,
  type AccountInfo,
  type ImportRecord,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { formatDate, formatDateTime } from '../lib/format'
import { AccountForm } from './AccountForm'
import { Icon } from './Icon'

type Notice = { tone: 'ok' | 'error'; text: string } | null

function importRange(record: ImportRecord): string {
  if (!record.date_from || !record.date_to) return 'nothing new'
  return record.date_from === record.date_to
    ? formatDate(record.date_from)
    : `${formatDate(record.date_from)} – ${formatDate(record.date_to)}`
}

/** An account's past imports, newest first, each with Undo. */
function ImportHistory({ accountId, onChanged }: { accountId: string; onChanged: () => void }) {
  const [records, setRecords] = useState<ImportRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    listImports(accountId)
      .then(setRecords)
      .catch((failure: unknown) => setError(errorMessages(failure).join(' ')))
  }, [accountId])
  useEffect(load, [load])

  const undo = async (record: ImportRecord) => {
    if (!window.confirm(`Undo the import of ${record.filename || 'this file'}? Its ${record.added} transactions are removed.`))
      return
    setBusy(true)
    try {
      await undoImport(record.import_id)
      load()
      onChanged()
    } catch (failure) {
      setError(errorMessages(failure).join(' '))
    } finally {
      setBusy(false)
    }
  }

  if (error)
    return (
      <p className="notice notice-error" role="alert">
        {error}
      </p>
    )
  if (records === null) return <p className="muted">Loading…</p>
  if (records.length === 0) return <p className="muted">No imports yet.</p>
  return (
    <ul className="import-history">
      {records.map((record) => (
        <li key={record.import_id}>
          <span className="menu-text">
            {record.filename || 'Statement'}
            <span className="muted">
              {importRange(record)} · {record.added} added · {formatDateTime(record.created_at)}
            </span>
          </span>
          <button
            type="button"
            className="button button-ghost button-small"
            onClick={() => void undo(record)}
            disabled={busy}
          >
            Undo
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Account page: accounts filled from statement files the user downloads from their bank. */
export function StatementAccounts() {
  const { user } = useAuth()
  const [accounts, setAccounts] = useState<AccountInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string[] | null>(null)
  // The account being edited, "new" while adding one, or null.
  const [editing, setEditing] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice>(null)
  const load = useCallback(() => {
    listAccounts()
      .then((result) => {
        setAccounts(result.filter((info) => !info.linked))
        setLoadError(null)
      })
      .catch((error: unknown) => setLoadError(errorMessages(error)))
  }, [])

  if (loadError) {
    return (
      <div className="card">
        <p className="notice notice-error" role="alert">
          {loadError.join(' ')}
        </p>
      </div>
    )
  }
  if (accounts === null) return null
  const defaultCurrency = user?.default_currency ?? 'CAD'

  return (
    <section className="card bank-links" aria-labelledby="statement-accounts-title">
      <div className="card-head">
        <h2 id="statement-accounts-title" className="card-title">
          Accounts & cards
        </h2>
        {accounts.length > 0 && (
          <Link to="/import" className="chip chip-action">
            <Icon name="download" size={16} />
            Import
          </Link>
        )}
      </div>
      <p className="muted bank-note">
        Download a statement from your bank's website (CSV, or OFX/QFX) and import it. LoonieToonie never signs in to your
        bank.
      </p>

      {accounts.length > 0 && (
        <ul className="bank-items">
          {accounts.map((info) => (
            <li key={info.account.account_id} className="bank-item">
              {editing === info.account.account_id ? (
                <AccountForm
                  account={info.account}
                  defaultCurrency={defaultCurrency}
                  onSaved={() => {
                    setEditing(null)
                    load()
                  }}
                  onCancel={() => setEditing(null)}
                  onRemoved={(_, withTransactions) => {
                    setEditing(null)
                    setNotice({
                      tone: 'ok',
                      text: `${info.account.name || 'The account'} was removed${withTransactions ? ' with its transactions' : ''}.`,
                    })
                    load()
                  }}
                />
              ) : (
                <>
                  <div className="bank-item-head">
                    <span className="menu-icon">
                      <Icon name={info.account.type === 'credit' ? 'card' : 'bank'} />
                    </span>
                    <span className="menu-text">
                      {info.account.name}
                      {info.account.mask && ` •• ${info.account.mask}`}
                      <span className="muted">
                        {info.account.institution}
                        {info.last_import?.date_to
                          ? ` · up to ${formatDate(info.last_import.date_to)}`
                          : ' · nothing imported yet'}
                      </span>
                    </span>
                    <div className="card-head-actions">
                      <button
                        type="button"
                        className="button button-ghost button-small"
                        onClick={() => setEditing(info.account.account_id)}
                      >
                        <Icon name="pencil" size={16} />
                        Edit
                      </button>
                      <Link
                        to={`/import?account=${encodeURIComponent(info.account.account_id)}`}
                        className="button button-secondary button-small"
                      >
                        Import
                      </Link>
                    </div>
                  </div>
                  <details className="account-details">
                    <summary>Import history</summary>
                    <ImportHistory accountId={info.account.account_id} onChanged={load} />
                  </details>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {notice && (
        <p className={`notice notice-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}

      {editing === 'new' ? (
        <AccountForm
          defaultCurrency={defaultCurrency}
          onSaved={() => {
            setEditing(null)
            load()
          }}
          onCancel={() => setEditing(null)}
        />
      ) : (
        <button type="button" className="button button-secondary button-block" onClick={() => setEditing('new')}>
          <Icon name="plus" size={20} />
          Add an account or card
        </button>
      )}
    </section>
  )
}
