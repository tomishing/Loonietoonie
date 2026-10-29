import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { API_BASE_URL, exchangeAppCode, getCurrentUser, LOGIN_URL, logout, type User } from '../api/client'
import { isNativeApp } from '../platform'
import { readCallback, setAppToken, startAppSignIn } from './appAuth'
import { AuthContext, type AuthState, type AuthStatus } from './AuthContext'

const SIGN_IN_ERRORS: Record<string, string> = {
  access_denied: 'Sign-in was cancelled.',
  drive_permission_required:
    'LoonieToonie needs permission to create its own files in your Google Drive. Please sign in again and allow it.',
  invalid_state: 'Sign-in took too long or was interrupted. Please try again.',
  not_invited:
    "LoonieToonie Tracker is invite-only for now, and this Google account isn't on the list. Sign in with the account you were invited with, or ask for an invitation.",
  google_error: 'Google sign-in failed. Please try again.',
  storage_error: "Signed in, but we couldn't set up your LoonieToonie spreadsheet in Google Drive. Please try again.",
}

const errorMessage = (code: string) => SIGN_IN_ERRORS[code] ?? SIGN_IN_ERRORS.google_error

/** Reads ?auth_error= left by the backend redirect (web), then removes it from the URL. */
function takeSignInError(): string | null {
  const url = new URL(window.location.href)
  const code = url.searchParams.get('auth_error')
  if (code === null) {
    return null
  }
  url.searchParams.delete('auth_error')
  window.history.replaceState(null, '', url)
  return errorMessage(code)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<User | null>(null)
  const [signInError, setSignInError] = useState<string | null>(takeSignInError)

  // Re-reads the user after an app sign-in.
  const refresh = useCallback(async () => {
    try {
      const current = await getCurrentUser()
      setUser(current)
      setStatus(current ? 'signed-in' : 'signed-out')
    } catch {
      setStatus('error')
    }
  }, [])

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

  // Android: Google Sign-In finishes in the system browser and comes back as a deep link.
  useEffect(() => {
    if (!isNativeApp) return
    let removed = false
    let remove: (() => void) | undefined
    void import('@capacitor/app').then(({ App }) =>
      App.addListener('appUrlOpen', async ({ url }) => {
        const result = await readCallback(url)
        if (result === null) return
        const { Browser } = await import('@capacitor/browser')
        void Browser.close().catch(() => undefined)
        if ('error' in result) {
          setSignInError(errorMessage(result.error))
          return
        }
        try {
          await setAppToken(await exchangeAppCode(result.code, result.verifier))
          setSignInError(null)
          setStatus('loading')
          await refresh()
        } catch {
          setSignInError(errorMessage('invalid_state'))
        }
      }).then((handle) => {
        if (removed) void handle.remove()
        else remove = () => void handle.remove()
      }),
    )
    return () => {
      removed = true
      remove?.()
    }
  }, [refresh])

  const signIn = useCallback(() => {
    if (isNativeApp) {
      setSignInError(null)
      void startAppSignIn(API_BASE_URL)
    } else {
      window.location.assign(LOGIN_URL)
    }
  }, [])

  const signOut = useCallback(async () => {
    if (isNativeApp) {
      await setAppToken(null)
    } else {
      await logout()
    }
    setUser(null)
    setStatus('signed-out')
  }, [])

  const retry = useCallback(() => {
    setStatus('loading')
    void refresh()
  }, [refresh])

  const value = useMemo<AuthState>(
    () => ({ status, user, signInError, signIn, signOut, retry }),
    [status, user, signInError, signIn, signOut, retry],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
