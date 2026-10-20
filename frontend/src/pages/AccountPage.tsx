import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useAuth } from '../auth/AuthContext'
import { Avatar } from '../components/Avatar'
import { BankLinks } from '../components/BankLinks'
import { CategoryList } from '../components/CategoryList'
import { HouseholdForm } from '../components/HouseholdForm'
import { Icon } from '../components/Icon'

export function AccountPage() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [signOutFailed, setSignOutFailed] = useState(false)

  if (!user) return null

  const handleSignOut = () => {
    setSignOutFailed(false)
    signOut()
      .then(() => navigate('/', { replace: true }))
      .catch(() => setSignOutFailed(true))
  }

  return (
    <section className="page page-stack">
      <div className="page-title">
        <h1>Account</h1>
      </div>

      <div className="card profile">
        <Avatar user={user} size={56} />
        <div>
          <p className="profile-name">{user.name ?? user.email}</p>
          {user.name && <p className="muted">{user.email}</p>}
        </div>
      </div>

      <ul className="card menu">
        <li>
          <a className="menu-row" href={user.spreadsheet_url} target="_blank" rel="noreferrer">
            <span className="menu-icon">
              <Icon name="sheet" />
            </span>
            <span className="menu-text">
              My LoonieToonie spreadsheet
              <span className="muted">All your receipts, in your own Google Drive</span>
            </span>
            <Icon name="external" size={18} className="menu-chevron" />
          </a>
        </li>
      </ul>

      <HouseholdForm />

      <BankLinks />

      <div className="card">
        <CategoryList />
      </div>

      <button type="button" className="button button-danger button-block" onClick={handleSignOut}>
        <Icon name="logout" size={20} />
        Sign out
      </button>
      {signOutFailed && (
        <p className="notice notice-error" role="alert">
          Sign-out failed. Please try again.
        </p>
      )}
    </section>
  )
}
