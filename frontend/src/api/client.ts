const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

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
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    credentials: 'include',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
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

export function createReceipt(input: ReceiptInput): Promise<Receipt> {
  return apiPost<Receipt>('/api/receipts', input)
}

export function updateReceipt(receiptId: string, input: ReceiptInput): Promise<Receipt> {
  return apiPut<Receipt>(`/api/receipts/${encodeURIComponent(receiptId)}`, input)
}

export function deleteReceipt(receiptId: string): Promise<void> {
  return apiDelete(`/api/receipts/${encodeURIComponent(receiptId)}`)
}

export function logout(): Promise<void> {
  return apiPost<void>('/api/auth/logout')
}

/** Full-page navigation target that starts Google Sign-In on the backend. */
export const LOGIN_URL = `${API_BASE_URL}/api/auth/google`
