const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { credentials: 'include' })
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`)
  }
  return (await response.json()) as T
}

export interface HealthResponse {
  status: string
  version: string
}

export function getHealth(): Promise<HealthResponse> {
  return apiGet<HealthResponse>('/api/health')
}
