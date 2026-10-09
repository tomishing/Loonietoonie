import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { getCurrentUser, LOGIN_URL, logout, type User } from '../api/client'
import { AuthContext, type AuthState, type AuthStatus } from './AuthContext'

const SIGN_IN_ERRORS: Record<string, string> = {
  access_denied: 'Sign-in was cancelled.',
  drive_permission_required:
    'LoonieToonie needs permission to create its own files in your Google Drive. Please sign in again and allow it.',
  invalid_state: 'Sign-in took too long or was interrupted. Please try again.',
  google_error: 'Google sign-in failed. Please try again.',
}

/** Reads ?auth_error= left by the backend redirect, then removes it from the URL. */
function takeSignInError(): string | null {
  const url = new URL(window.location.href)
  const code = url.searchParams.get('auth_error')
  if (code === null) {
    return null
  }
  url.searchParams.delete('auth_error')
  window.history.replaceState(null, '', url)
  return SIGN_IN_ERRORS[code] ?? SIGN_IN_ERRORS.google_error
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<User | null>(null)
  const [signInError] = useState<string | null>(takeSignInError)

  useEffect(() => {
    let active = true
    getCurrentUser()
      .then((current) => {
        if (!active) return
        setUser(current)
        setStatus(current ? 'signed-in' : 'signed-out')
      })
      .catch(() => {
        if (active) setStatus('error')
      })
    return () => {
      active = false
    }
  }, [])

  const signIn = useCallback(() => {
    window.location.assign(LOGIN_URL)
  }, [])

  const signOut = useCallback(async () => {
    await logout()
    setUser(null)
    setStatus('signed-out')
  }, [])

  const value = useMemo<AuthState>(
    () => ({ status, user, signInError, signIn, signOut }),
    [status, user, signInError, signIn, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
