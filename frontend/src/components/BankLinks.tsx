import { useCallback, useEffect, useState } from 'react'
import { usePlaidLink } from 'react-plaid-link'
import {
  createBankLinkToken,
  errorMessages,
  getBankStatus,
  linkBank,
  syncBanks,
  unlinkBank,
  type BankStatus,
} from '../api/client'
import { formatDateTime } from '../lib/format'
import { describeSync, syncWithRefresh, type SyncNotice } from './bankSync'
import { Icon } from './Icon'

const FIRST_IMPORT_CHECKS = 4
const FIRST_IMPORT_WAIT_MS = 5000

type Notice = SyncNotice | null

/** Opens Plaid Link as soon as the script has loaded. Mounted only while linking. */
function PlaidLinkOpener({
  token,
  onSuccess,
  onExit,
}: {
  token: string
  onSuccess: (publicToken: string) => void
  onExit: () => void
}) {
  const { open, ready } = usePlaidLink({
    token,
    onSuccess: (publicToken) => (publicToken ? onSuccess(publicToken) : onExit()),
    onExit: () => onExit(),
  })
  useEffect(() => {
    if (ready) open()
  }, [ready, open])
  return null
}

/** Account page: link banks and credit cards through Plaid, see them, sync, and unlink. */
export function BankLinks() {
  const [status, setStatus] = useState<BankStatus | null>(null)
  const [loadError, setLoadError] = useState<string[] | null>(null)
  const [linkToken, setLinkToken] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice>(null)

  const load = useCallback(() => {
    getBankStatus()
      .then((result) => {
        setStatus(result)
        setLoadError(null)
      })
      .catch((error: unknown) => setLoadError(errorMessages(error)))
  }, [])

  useEffect(load, [load])

  const startLink = async () => {
    setNotice(null)
    setBusy('Opening Plaid…')
    try {
      setLinkToken(await createBankLinkToken())
    } catch (error) {
      setNotice({ tone: 'error', text: errorMessages(error).join(' ') })
      setBusy(null)
    }
  }

  const finishLink = async (publicToken: string) => {
    setLinkToken(null)
    setBusy('Importing your transactions…')
    try {
      const result = await linkBank(publicToken)
      let results = [result]
      // A new link's history takes a few seconds to arrive at Plaid; check back a few times.
      for (let attempt = 0; attempt < FIRST_IMPORT_CHECKS && result.status === 'ok'; attempt += 1) {
        if (results.some((r) => r.changes.added > 0)) break
        setBusy(`Waiting for ${result.institution || 'your bank'} to send your transactions…`)
        await new Promise((resolve) => setTimeout(resolve, FIRST_IMPORT_WAIT_MS))
        results = (await syncBanks()).filter((r) => r.item_id === result.item_id)
      }
      const imported = results.some((r) => r.changes.added > 0)
      setNotice(
        imported || result.status !== 'ok'
          ? describeSync({ results, added: results.reduce((sum, r) => sum + r.changes.added, 0), asked: [] })
          : {
              tone: 'ok',
              text: `${result.institution || 'Your bank'} is linked. Its transactions can take a few minutes to arrive; tap Sync now later.`,
            },
      )
    } catch (error) {
      setNotice({ tone: 'error', text: errorMessages(error).join(' ') })
    } finally {
      setBusy(null)
      load()
    }
  }

  const sync = async () => {
    setNotice(null)
    setBusy('Updating…')
    try {
      setNotice(describeSync(await syncWithRefresh(setBusy)))
    } catch (error) {
      setNotice({ tone: 'error', text: errorMessages(error).join(' ') })
    } finally {
      setBusy(null)
      load()
    }
  }

  const unlink = async (itemId: string, institution: string) => {
    if (
      !window.confirm(
        `Unlink ${institution || 'this bank'}? LoonieToonie stops importing from it.`,
      )
    )
      return
    // A second question: test (Sandbox) or unwanted data can be removed with the link.
    const removeTransactions = window.confirm(
      `Also remove the transactions imported from ${institution || 'this bank'}?\n\nOK: remove them from your spending and spreadsheet (marked deleted).\nCancel: keep them.`,
    )
    setBusy('Unlinking…')
    try {
      await unlinkBank(itemId, removeTransactions)
      setNotice({
        tone: 'ok',
        text: `${institution || 'The bank'} was unlinked${removeTransactions ? ' and its transactions removed' : ''}.`,
      })
    } catch (error) {
      setNotice({ tone: 'error', text: errorMessages(error).join(' ') })
    } finally {
      setBusy(null)
      load()
    }
  }

  if (loadError) {
    return (
      <div className="card">
        <p className="notice notice-error" role="alert">
          {loadError.join(' ')}
        </p>
      </div>
    )
  }
  if (status === null || !status.enabled) return null

  return (
    <section className="card bank-links" aria-labelledby="bank-links-title">
      <div className="card-head">
        <h2 id="bank-links-title" className="card-title">
          Bank accounts & cards
        </h2>
        {status.items.length > 0 && (
          <button type="button" className="chip chip-action" onClick={() => void sync()} disabled={busy !== null}>
            <Icon name="refresh" size={16} />
            Sync now
          </button>
        )}
      </div>
      <p className="muted bank-note">
        Transactions are imported into your spreadsheet. Your bank sign-in goes to Plaid, never to LoonieToonie, and
        access can be removed at any time.
      </p>

      {status.items.length > 0 && (
        <ul className="bank-items">
          {status.items.map((item) => (
            <li key={item.item_id} className="bank-item">
              <div className="bank-item-head">
                <span className="menu-icon">
                  <Icon name="bank" />
                </span>
                <span className="menu-text">
                  {item.institution || 'Linked bank'}
                  <span className="muted">
                    {item.bank_updated_at
                      ? `Bank last read ${formatDateTime(item.bank_updated_at)}`
                      : item.last_synced_at
                        ? `Synced ${formatDateTime(item.last_synced_at)}`
                        : 'Not synced yet'}
                  </span>
                </span>
                <button
                  type="button"
                  className="button button-ghost button-small"
                  onClick={() => void unlink(item.item_id, item.institution)}
                  disabled={busy !== null}
                >
                  Unlink
                </button>
              </div>
              <ul className="bank-accounts">
                {item.accounts.map((account) => (
                  <li key={account.account_id}>
                    <Icon name={account.type === 'credit' ? 'card' : 'bank'} size={16} />
                    <span>{account.name}</span>
                    {account.mask && <span className="muted">•• {account.mask}</span>}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}

      {notice && (
        <p className={`notice notice-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}
      {busy && (
        <p className="muted" role="status">
          {busy}
        </p>
      )}

      <button
        type="button"
        className="button button-secondary button-block"
        onClick={() => void startLink()}
        disabled={busy !== null}
      >
        <Icon name="plus" size={20} />
        Link a bank or credit card
      </button>

      {linkToken && (
        <PlaidLinkOpener
          token={linkToken}
          onSuccess={(publicToken) => void finishLink(publicToken)}
          onExit={() => {
            setLinkToken(null)
            setBusy(null)
          }}
        />
      )}
    </section>
  )
}
