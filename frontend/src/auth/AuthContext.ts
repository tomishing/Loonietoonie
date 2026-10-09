import { createContext, useContext } from 'react'
import type { User } from '../api/client'

export type AuthStatus = 'loading' | 'signed-in' | 'signed-out' | 'error'

export interface AuthState {
  status: AuthStatus
  user: User | null
  /** Message for a failed sign-in (from the ?auth_error= redirect), if any. */
  signInError: string | null
  signIn: () => void
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (auth === null) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return auth
}
