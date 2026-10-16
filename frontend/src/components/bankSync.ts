import { syncBanks, type ItemSyncResult } from '../api/client'
import { formatDateTime } from '../lib/format'

export type SyncNotice = { tone: 'ok' | 'warn' | 'error'; text: string }

export interface SyncOutcome {
  results: ItemSyncResult[]
  /** New transactions over all the syncs this run made. */
  added: number
  /** Banks Plaid was asked to read now. */
  asked: string[]
}

const CHECKS = 4
const WAIT_MS = 8000

const countAdded = (results: ItemSyncResult[]) => results.reduce((sum, r) => sum + r.changes.added, 0)
const names = (results: ItemSyncResult[]) => results.map((r) => r.institution || 'your bank')

/**
 * The Sync button: ask Plaid to read the banks now and import. Plaid reads them in the
 * background, so when nothing new came in yet, check back a few times. `onWaiting` reports it.
 */
export async function syncWithRefresh(onWaiting?: (text: string) => void): Promise<SyncOutcome> {
  let results = await syncBanks(true)
  let added = countAdded(results)
  const asked = names(results.filter((r) => r.refresh_requested))
  for (let check = 0; check < CHECKS && asked.length > 0 && added === 0; check += 1) {
    onWaiting?.(`Asked ${asked.join(', ')} for new transactions…`)
    await new Promise((resolve) => setTimeout(resolve, WAIT_MS))
    results = await syncBanks()
    added += countAdded(results)
  }
  return { results, added, asked }
}

/** What to tell the user after a sync. */
export function describeSync({ results, added, asked }: SyncOutcome): SyncNotice {
  const relink = results.filter((r) => r.status === 'needs_relink')
  const failed = results.filter((r) => r.status === 'error')
  if (relink.length) return { tone: 'warn', text: `${names(relink).join(', ')} needs you to unlink it and link it again.` }
  if (failed.length) return { tone: 'error', text: `Couldn't update ${names(failed).join(', ')}. Try again later.` }
  if (added) return { tone: 'ok', text: `Imported ${added} new transaction${added === 1 ? '' : 's'}.` }
  // Nothing new: say when Plaid last read each bank, so "up to date" isn't misleading.
  const read = results
    .filter((r) => r.bank_updated_at)
    .map((r) => `${r.institution || 'your bank'} on ${formatDateTime(r.bank_updated_at)}`)
  return {
    tone: 'ok',
    text:
      `No new transactions yet.${read.length ? ` Plaid last read ${read.join(' and ')}.` : ''}` +
      (asked.length
        ? ' The bank may still be sending them; try again in a few minutes.'
        : ' The bank can be asked again an hour after the last time.'),
  }
}
