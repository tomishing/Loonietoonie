import { NavLink, Outlet } from 'react-router'
import { useAuth } from '../auth/AuthContext'
import { LoginPage, SplashScreen } from '../pages/LoginPage'
import { Avatar } from './Avatar'
import { Icon, type IconName } from './Icon'

const TABS: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: '/', label: 'Patterns', icon: 'chart', end: true },
  { to: '/transactions', label: 'Transactions', icon: 'list' },
  { to: '/ask', label: 'Ask', icon: 'chat' },
  { to: '/account', label: 'Account', icon: 'user' },
]

/** App shell. Every page needs a signed-in user; anyone else sees the login page. */
export function Layout() {
  const { status, user } = useAuth()

  if (status === 'loading') return <SplashScreen />
  if (status === 'error') return <SplashScreen error />
  if (status === 'signed-out' || !user) return <LoginPage />

  const tab = ({ to, label, icon, end }: (typeof TABS)[number]) => (
    <NavLink key={to} to={to} end={end} className="tab">
      <span className="tab-icon">
        <Icon name={icon} />
      </span>
      <span className="tab-label">{label}</span>
    </NavLink>
  )

  return (
    <div className="app">
      <header className="app-header">
        <NavLink to="/" className="brand">
          <img src="/loon-logo.png" alt="" width={31} height={40} />
          <span>LoonieToonie</span>
        </NavLink>
        <nav className="top-nav" aria-label="Main">
          {TABS.slice(0, 3).map(tab)}
          <NavLink to="/receipts/new" className="tab">
            <span className="tab-icon">
              <Icon name="plus" />
            </span>
            <span className="tab-label">Add</span>
          </NavLink>
        </nav>
        <NavLink to="/scan" className="header-scan">
          <Icon name="camera" size={20} />
          Scan
        </NavLink>
        <NavLink to="/account" className="header-avatar" aria-label="Account">
          <Avatar user={user} size={36} />
        </NavLink>
      </header>

      <main className="app-main">
        <Outlet />
      </main>

      {/* Phones: bottom tab bar with the Scan button in the middle. */}
      <nav className="tab-bar" aria-label="Main">
        {TABS.slice(0, 2).map(tab)}
        <NavLink to="/scan" className="tab tab-scan" aria-label="Scan a receipt">
          <span className="tab-scan-button">
            <Icon name="camera" size={26} />
          </span>
          <span className="tab-label">Scan</span>
        </NavLink>
        {TABS.slice(2).map(tab)}
      </nav>
    </div>
  )
}
