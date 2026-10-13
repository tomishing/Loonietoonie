import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

/** Shows its children only to signed-in users; otherwise a sign-in prompt. */
export function RequireSignIn({ children }: { children: ReactNode }) {
  const { status, signIn } = useAuth()

  if (status === 'loading') {
    return <p className="muted center">Loading…</p>
  }
  if (status === 'error') {
    return (
      <p className="notice notice-error center" role="alert">
        Can't reach the LoonieToonie server. Check your connection and try again.
      </p>
    )
  }
  if (status === 'signed-out') {
    return (
      <div className="center stack">
        <p>Please sign in to see your receipts.</p>
        <button type="button" className="button button-primary" onClick={signIn}>
          Sign in with Google
        </button>
      </div>
    )
  }
  return <>{children}</>
}
