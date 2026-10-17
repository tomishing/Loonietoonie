import { getAppToken } from '../auth/appAuth'
import { isNativeApp } from '../platform'

// In the Android app the backend is at an absolute URL. During development the phone
// reaches this computer's backend through `adb reverse tcp:8000 tcp:8000`.
const NATIVE_DEV_API_BASE_URL = 'http://localhost:8000'
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || (isNativeApp ? NATIVE_DEV_API_BASE_URL : '')

/** Web: the session cookie. Android app: the session token as a bearer header (no cookies). */
async function withAuth(init: RequestInit = {}): Promise<RequestInit> {
  if (!isNativeApp) return { ...init, credentials: 'include' }
  const token = await getAppToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return { ...init, headers, credentials: 'omit' }
}

export class ApiError extends Error {
  readonly status: number
  /** FastAPI error detail: a message string, or a list of field errors for 422 responses. */
  readonly detail: unknown

  constructor(method: string, path: string, status: number, detail: unknown) {
    super(`${method} ${path} failed: ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isBlob = body instanceof Blob
  const response = await fetch(
    `${API_BASE_URL}${path}`,
    await withAuth({
      method,
      headers: body === undefined ? undefined : { 'Content-Type': isBlob ? body.type : 'application/json' },
      body: body === undefined ? undefined : isBlob ? body : JSON.stringify(body),
    }),
  )
  if (!response.ok) {
    let detail: unknown = null
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail ?? null
    } catch {
      // Not JSON (e.g. a proxy error page).
    }
    throw new ApiError(method, path, response.status, detail)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>('GET', path)
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('POST', path, body)
}

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return request<T>('PUT', path, body)
}

export function apiDelete(path: string): Promise<void> {
  return request<void>('DELETE', path)
}

interface FieldError {
  loc: (string | number)[]
  msg: string
}

const FIELD_LABELS: Record<string, string> = {
  store: 'Store',
  purchased_at: 'Date',
  total: 'Total',
  currency: 'Currency',
  items: 'Items',
  name: 'name',
  quantity: 'quantity',
  price: 'price',
  category: 'category',
  subcategory: 'subcategory',
}

/** Human-readable messages for an API error, e.g. "Item 2 price: Use at most 3 decimal places". */
export function errorMessages(error: unknown): string[] {
  if (!(error instanceof ApiError)) {
    return ["Can't reach the LoonieToonie server. Check your connection and try again."]
  }
  if (typeof error.detail === 'string') {
    return [error.detail]
  }
  if (Array.isArray(error.detail)) {
    return (error.detail as FieldError[]).map(({ loc, msg }) => {
      const path = loc.filter((part) => part !== 'body')
      const message = msg.replace(/^Value error, /, '')
      if (path[0] === 'items' && typeof path[1] === 'number') {
        const field = path[2] === undefined ? '' : ` ${FIELD_LABELS[String(path[2])] ?? path[2]}`
        return `Item ${path[1] + 1}${field}: ${message}`
      }
      const field = FIELD_LABELS[String(path[0])] ?? String(path[0] ?? '')
      return field ? `${field}: ${message}` : message
    })
  }
  return ['Something went wrong. Please try again.']
}

export interface HealthResponse {
  status: string
  version: string
}

export function getHealth(): Promise<HealthResponse> {
  return apiGet<HealthResponse>('/api/health')
}

export interface User {
  email: string
  name: string | null
  picture: string | null
  spreadsheet_url: string
  default_currency: string
}

/** The signed-in user, or null when there is no valid session. */
export async function getCurrentUser(): Promise<User | null> {
  try {
    return await apiGet<User>('/api/auth/me')
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null
    }
    throw error
  }
}

export interface Category {
  name: string
  icon: string
  subcategories: string[]
  /** Subcategory name → what it covers (only the ones with a description). */
  descriptions: Record<string, string>
}

export function getCategories(): Promise<Category[]> {
  return apiGet<Category[]>('/api/categories')
}

// Money values are decimal strings (e.g. "3.49"), never JS numbers.
export interface Item {
  item_id: string
  name: string
  quantity: string
  price: string
  category: string
  /** "" when the item has no subcategory. */
  subcategory: string
}

export interface Receipt {
  receipt_id: string
  store: string
  purchased_at: string
  total: string
  currency: string
  image_link: string
  created_at: string
  updated_at: string
  items: Item[]
}

export interface ItemInput {
  item_id?: string
  name: string
  quantity: string
  price: string
  category: string
  /** Optional; must be one of the category's subcategories. */
  subcategory?: string | null
}

export interface ReceiptInput {
  store: string
  purchased_at: string
  total: string
  currency: string
  items: ItemInput[]
}

export interface ReceiptFilters {
  from?: string
  to?: string
  category?: string
  subcategory?: string
}

export function listReceipts(filters: ReceiptFilters = {}): Promise<Receipt[]> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value)
  }
  const query = params.toString()
  return apiGet<Receipt[]>(`/api/receipts${query ? `?${query}` : ''}`)
}

export function getReceipt(receiptId: string): Promise<Receipt> {
  return apiGet<Receipt>(`/api/receipts/${encodeURIComponent(receiptId)}`)
}

/** A saved receipt that looks like the same purchase (409 from createReceipt). */
export interface DuplicateReceipt {
  receipt_id: string
  store: string
  purchased_at: string
  total: string
  currency: string
}

/** The saved receipt behind a 409 "looks like a duplicate" answer, or null for other errors. */
export function duplicateOf(error: unknown): DuplicateReceipt | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const detail = error.detail as { duplicate?: DuplicateReceipt } | null
  return detail?.duplicate ?? null
}

/** Save a new receipt. Without allowDuplicate, a likely duplicate of a saved one fails with 409. */
export function createReceipt(input: ReceiptInput, allowDuplicate = false): Promise<Receipt> {
  return apiPost<Receipt>(`/api/receipts${allowDuplicate ? '?allow_duplicate=true' : ''}`, input)
}

export function updateReceipt(receiptId: string, input: ReceiptInput): Promise<Receipt> {
  return apiPut<Receipt>(`/api/receipts/${encodeURIComponent(receiptId)}`, input)
}

/** Saves the receipt photo to the user's Drive (LoonieToonie/receipts/). */
export function uploadReceiptImage(receiptId: string, image: Blob): Promise<Receipt> {
  return apiPut<Receipt>(`/api/receipts/${encodeURIComponent(receiptId)}/image`, image)
}

// --- scanning ---------------------------------------------------------------

export interface ScanItem {
  name: string
  quantity: string | null
  price: string | null
  category: string
  subcategory: string | null
}

export interface ScanDraft {
  store: string
  purchased_at: string | null
  currency: string
  total: string | null
  items: ScanItem[]
}

export interface ScanIssue {
  code: string
  message: string
}

export interface ScanResult {
  draft: ScanDraft
  issues: ScanIssue[]
  /** The reading looks wrong: send the photo once so Claude can look at it. */
  needs_image: boolean
  used_image: boolean
}

/** Reads OCR text into a draft. `images` (the photo, or the parts of a long receipt in order) only on the retry. */
export function scanReceipt(ocrText: string, images?: { base64: string; mediaType: string }[]): Promise<ScanResult> {
  return apiPost<ScanResult>('/api/receipts/scan', {
    ocr_text: ocrText,
    ...(images?.length ? { images: images.map((image) => ({ data: image.base64, media_type: image.mediaType })) } : {}),
  })
}

export function deleteReceipt(receiptId: string): Promise<void> {
  return apiDelete(`/api/receipts/${encodeURIComponent(receiptId)}`)
}

export function logout(): Promise<void> {
  return apiPost<void>('/api/auth/logout')
}

/** Full-page navigation target that starts Google Sign-In on the backend (web). */
export const LOGIN_URL = `${API_BASE_URL}/api/auth/google`

/** Android: exchange the deep-link code and PKCE verifier for a session token. */
export async function exchangeAppCode(code: string, verifier: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/auth/app/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, verifier }),
  })
  if (!response.ok) throw new ApiError('POST', '/api/auth/app/token', response.status, null)
  return ((await response.json()) as { token: string }).token
}

// --- dashboard ----------------------------------------------------------------

export interface CategorySpending {
  category: string
  total: string
  previous_total: string
  average_total: string | null
  share: string
  /** Drill-down; empty when none of the category's items have a subcategory. */
  subcategories: SubcategorySpending[]
}

export interface SubcategorySpending {
  /** "" for items without a subcategory. */
  subcategory: string
  total: string
  average_total: string | null
}

export interface TrendMonth {
  month: string
  total: string
  by_category: Record<string, string>
}

export interface Change {
  kind: 'category_up' | 'category_down' | 'category_new' | 'new_store'
  category: string | null
  store: string | null
  amount: string
  baseline: string | null
}

export interface Summary {
  currency: string
  currencies: string[]
  period: { month: string; start: string; end: string; partial: boolean }
  total: string
  receipt_count: number
  /** Of receipt_count: card/bank charges without a receipt. */
  bank_transaction_count: number
  previous_total: string
  average_total: string | null
  categories: CategorySpending[]
  trend: TrendMonth[]
  trend_categories: string[]
  top_stores: { store: string; total: string; visits: number }[]
  top_items: { name: string; times: number; total: string }[]
  changes: Change[]
}

/** Category name used for amounts on a receipt that its items don't cover. */
export const NOT_ITEMIZED = '(not itemized)'

export function getSummary(month: string, currency?: string): Promise<Summary> {
  const params = new URLSearchParams({ month })
  if (currency) params.set('currency', currency)
  return apiGet<Summary>(`/api/summary?${params}`)
}

// --- chat ---------------------------------------------------------------------

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export type ChatEvent =
  | { type: 'text'; text: string }
  | { type: 'status'; text: string }
  | { type: 'reset'; text: string }
  | { type: 'error'; text: string }
  | { type: 'done'; text: string }

/** Streams the assistant's answer. Events arrive as newline-delimited JSON. */
export async function streamChat(
  messages: ChatTurn[],
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/chat`,
    await withAuth({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
      signal,
    }),
  )
  if (!response.ok || response.body === null) {
    let detail: unknown = null
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail ?? null
    } catch {
      // not JSON
    }
    throw new ApiError('POST', '/api/chat', response.status, detail)
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as ChatEvent)
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as ChatEvent)
}

// --- export -------------------------------------------------------------------

/** Android app: download a CSV export with the bearer token and open the share sheet (save, email, Drive…). */
export async function shareCsvExport(kind: 'items' | 'receipts', filters: ReceiptFilters = {}): Promise<void> {
  const url = exportCsvUrl(kind, filters)
  const response = await fetch(url, await withAuth())
  if (!response.ok) throw new ApiError('GET', '/api/export/csv', response.status, null)
  const filename = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? `loonietoonie-${kind}.csv`
  const [{ Filesystem, Directory, Encoding }, { Share }] = await Promise.all([
    import('@capacitor/filesystem'),
    import('@capacitor/share'),
  ])
  const saved = await Filesystem.writeFile({
    path: filename,
    data: await response.text(),
    directory: Directory.Cache,
    encoding: Encoding.UTF8,
  })
  await Share.share({ title: filename, files: [saved.uri] })
}

/** Download link for a CSV export: one row per item or per receipt, with the same filters as the list. */
export function exportCsvUrl(kind: 'items' | 'receipts', filters: ReceiptFilters = {}): string {
  const params = new URLSearchParams({ kind })
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value)
  }
  return `${API_BASE_URL}/api/export/csv?${params}`
}

// --- bank and card linking (phase 10a) -----------------------------------------

export interface BankAccount {
  account_id: string
  item_id: string
  institution: string
  name: string
  /** Last 4 digits, or "". */
  mask: string
  type: string
  subtype: string
  currency: string
}

export interface LinkedInstitution {
  item_id: string
  institution: string
  last_synced_at: string
  /** When Plaid last read the bank (ISO 8601, "" if unknown). */
  bank_updated_at: string
  /** The bank asked for a new sign-in: offer Reconnect (keeps the link and its transactions). */
  needs_reconnect: boolean
  accounts: BankAccount[]
}

export interface BankStatus {
  /** False when the server has no Plaid keys: the feature is hidden. */
  enabled: boolean
  items: LinkedInstitution[]
}

export interface BankChanges {
  added: number
  modified: number
  removed: number
}

export interface ItemSyncResult {
  item_id: string
  institution: string
  status: 'ok' | 'needs_relink' | 'error'
  changes: BankChanges
  error: string | null
  bank_updated_at: string
  /** This sync asked Plaid to read the bank now; new transactions follow shortly. */
  refresh_requested: boolean
}

export interface BankTransaction {
  transaction_id: string
  account_id: string
  date: string
  merchant: string
  description: string
  /** Decimal string: > 0 money spent, < 0 refund or money in. */
  amount: string
  currency: string
  /** Only "spending" counts towards spending patterns. */
  kind: 'spending' | 'income' | 'transfer'
  category: string
  subcategory: string
  plaid_category: string
  pending: boolean
  receipt_id: string
}

export function getBankStatus(): Promise<BankStatus> {
  return apiGet<BankStatus>('/api/bank/items')
}

/** A Plaid Link token for linking a new bank, or with itemId, for signing in again to a linked one. */
export async function createBankLinkToken(itemId?: string): Promise<string> {
  const body = itemId ? { item_id: itemId } : undefined
  return (await apiPost<{ link_token: string }>('/api/bank/link-token', body)).link_token
}

export function linkBank(publicToken: string): Promise<ItemSyncResult> {
  return apiPost<ItemSyncResult>('/api/bank/items', { public_token: publicToken })
}

/** Unlink a bank; with removeTransactions its imported transactions are removed from the Sheet too. */
export function unlinkBank(itemId: string, removeTransactions = false): Promise<void> {
  const query = removeTransactions ? '?remove_transactions=true' : ''
  return apiDelete(`/api/bank/items/${encodeURIComponent(itemId)}${query}`)
}

export interface ReceiptCandidate {
  receipt_id: string
  store: string
  purchased_at: string
  total: string
  currency: string
  /** The automatic rules would accept it. */
  likely: boolean
}

export function receiptCandidates(transactionId: string): Promise<ReceiptCandidate[]> {
  return apiGet<ReceiptCandidate[]>(`/api/bank/transactions/${encodeURIComponent(transactionId)}/candidates`)
}

/** Match a charge to a receipt, or (null) mark it as having no receipt. */
export function chooseReceipt(transactionId: string, receiptId: string | null): Promise<void> {
  return apiPut<void>(`/api/bank/transactions/${encodeURIComponent(transactionId)}/receipt`, {
    receipt_id: receiptId,
  })
}

/** Import from linked banks. With refresh, also ask Plaid to read the banks now (at most hourly). */
export async function syncBanks(refresh = false): Promise<ItemSyncResult[]> {
  return (await apiPost<{ items: ItemSyncResult[] }>(`/api/bank/sync${refresh ? '?refresh=true' : ''}`)).items
}

export function listBankTransactions(filters: { from?: string; to?: string; category?: string } = {}): Promise<BankTransaction[]> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value)
  }
  const query = params.toString()
  return apiGet<BankTransaction[]>(`/api/bank/transactions${query ? `?${query}` : ''}`)
}

/**
 * Change a charge's category (or mark it as not spending). With applyToMerchant, every charge from
 * the same merchant changes too, and new ones get it when imported. Returns how many changed.
 */
export async function setBankCategory(
  transactionId: string,
  choice: { kind: BankTransaction['kind']; category: string; subcategory: string; applyToMerchant: boolean },
): Promise<number> {
  const result = await apiPut<{ changed: number }>(
    `/api/bank/transactions/${encodeURIComponent(transactionId)}/category`,
    {
      kind: choice.kind,
      category: choice.category,
      subcategory: choice.subcategory,
      apply_to_merchant: choice.applyToMerchant,
    },
  )
  return result.changed
}
