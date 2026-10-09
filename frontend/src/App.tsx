import { useState } from 'react'
import { useAuth } from './auth/AuthContext'
import './App.css'

function App() {
  const { status, user, signInError, signIn, signOut } = useAuth()
  const [signOutFailed, setSignOutFailed] = useState(false)

  const handleSignOut = () => {
    setSignOutFailed(false)
    signOut().catch(() => setSignOutFailed(true))
  }

  return (
    <main className="home">
      <img className="mascot" src="/favicon.svg" alt="LoonieToonie mascot" width={120} height={120} />
      <h1>LoonieToonie</h1>
      <p className="tagline">Scan receipts, track spending, and see how much you can save.</p>

      {signInError && (
        <p className="notice notice-error" role="alert">
          {signInError}
        </p>
      )}

      {status === 'loading' && <p className="muted">Loading…</p>}

      {status === 'error' && (
        <p className="notice notice-error" role="alert">
          Can't reach the LoonieToonie server. Check your connection and try again.
        </p>
      )}

      {status === 'signed-out' && (
        <button type="button" className="button button-google" onClick={signIn}>
          Sign in with Google
        </button>
      )}

      {status === 'signed-in' && user && (
        <section className="account">
          {user.picture && (
            <img
              className="avatar"
              src={user.picture}
              alt=""
              width={48}
              height={48}
              referrerPolicy="no-referrer"
            />
          )}
          <p>
            Hi, <strong>{user.name ?? user.email}</strong>!
          </p>
          <p className="muted">{user.email}</p>
          <button type="button" className="button button-secondary" onClick={handleSignOut}>
            Sign out
          </button>
          {signOutFailed && (
            <p className="notice notice-error" role="alert">
              Sign-out failed. Please try again.
            </p>
          )}
        </section>
      )}
    </main>
  )
}

export default App
