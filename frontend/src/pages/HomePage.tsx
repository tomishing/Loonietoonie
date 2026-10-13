import { useState } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth/AuthContext'
import { CategoryList } from '../components/CategoryList'

export function HomePage() {
  const { status, user, signInError, signIn, signOut } = useAuth()
  const [signOutFailed, setSignOutFailed] = useState(false)

  const handleSignOut = () => {
    setSignOutFailed(false)
    signOut().catch(() => setSignOutFailed(true))
  }

  return (
    <section className="home">
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
        <button type="button" className="button button-primary" onClick={signIn}>
          Sign in with Google
        </button>
      )}

      {status === 'signed-in' && user && (
        <section className="account">
          {user.picture && (
            <img className="avatar" src={user.picture} alt="" width={48} height={48} referrerPolicy="no-referrer" />
          )}
          <p>
            Hi, <strong>{user.name ?? user.email}</strong>!
          </p>
          <p className="muted">{user.email}</p>
          <div className="actions">
            <Link to="/receipts/new" className="button button-primary">
              + Add a receipt
            </Link>
            <Link to="/transactions" className="button button-secondary">
              See transactions
            </Link>
          </div>
          <a className="sheet-link" href={user.spreadsheet_url} target="_blank" rel="noreferrer">
            📊 Open my LoonieToonie spreadsheet
          </a>
          <CategoryList />
          <button type="button" className="button button-ghost" onClick={handleSignOut}>
            Sign out
          </button>
          {signOutFailed && (
            <p className="notice notice-error" role="alert">
              Sign-out failed. Please try again.
            </p>
          )}
        </section>
      )}
    </section>
  )
}
