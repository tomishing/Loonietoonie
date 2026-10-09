const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  readonly status: number

  constructor(method: string, path: string, status: number) {
    super(`${method} ${path} failed: ${status}`)
    this.status = status
  }
}

async function request<T>(method: string, path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method, credentials: 'include' })
  if (!response.ok) {
    throw new ApiError(method, path, response.status)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>('GET', path)
}

export function apiPost<T>(path: string): Promise<T> {
  return request<T>('POST', path)
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

export function logout(): Promise<void> {
  return apiPost<void>('/api/auth/logout')
}

/** Full-page navigation target that starts Google Sign-In on the backend. */
export const LOGIN_URL = `${API_BASE_URL}/api/auth/google`
