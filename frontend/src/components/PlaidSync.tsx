import { useEffect, useState } from 'react'
import { errorMessages, syncBanks, type LinkedInstitution } from '../api/client'
import { formatDateTime } from '../lib/format'
import { describeSync, syncWithRefresh, type SyncNotice } from './bankSync'
import { Icon } from './Icon'

// Banks are synced once per app session when the Bank & card view first opens; "Sync now" on
// Account forces it.
let syncedThisSession = false

/**
 * Web app only (phase 12): the Sync button and when Plaid last read each linked bank. Loaded lazily
 * and left out of the Android build, which offers statement import only.
 */
export default function PlaidSync({
  banks,
  onSynced,
}: {
  banks: LinkedInstitution[]
  /** Called after a sync, to reload the transactions and the banks' status. */
  onSynced: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<SyncNotice | null>(null)
  const [syncing, setSyncing] = useState(!syncedThisSession)

  useEffect(() => {
    if (syncedThisSession) return
    let active = true
    syncBanks()
      .then(() => {
        syncedThisSession = true
        if (active) onSynced()
      })
      .catch(() => undefined) // a failed sync still leaves the imported list on screen
      .finally(() => active && setSyncing(false))
    return () => {
      active = false
    }
  }, [onSynced])

  const runSync = async () => {
    setNotice(null)
    setBusy('Syncing with your banks…')
    try {
      setNotice(describeSync(await syncWithRefresh(setBusy)))
      onSynced()
    } catch (error) {
      setNotice({ tone: 'error', text: errorMessages(error).join(' ') })
    } finally {
      setBusy(null)
    }
  }

  const lastRead = banks
    .filter((bank) => bank.bank_updated_at)
    .map((bank) => `${bank.institution || 'Bank'} ${formatDateTime(bank.bank_updated_at)}`)
    .join(' · ')

  return (
    <>
      <div className="bank-sync-bar">
        <span className="muted">{lastRead ? `Last read from the bank: ${lastRead}` : 'Linked banks'}</span>
        <button
          type="button"
          className="chip chip-action"
          onClick={() => void runSync()}
          disabled={busy !== null || syncing}
        >
          <Icon name="refresh" size={16} />
          Sync
        </button>
      </div>
      {(busy || syncing) && (
        <p className="muted sync-note" role="status">
          {busy ?? 'Checking your banks for new transactions…'}
        </p>
      )}
      {notice && (
        <p className={`notice notice-${notice.tone} sync-notice`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}
    </>
  )
}
