import { NavLink, Outlet } from 'react-router'
import { useAuth } from '../auth/AuthContext'

export function Layout() {
  const { status } = useAuth()

  return (
    <div className="app">
      <header className="app-header">
        <NavLink to="/" className="brand">
          <img src="/favicon.svg" alt="" width={32} height={32} />
          <span>LoonieToonie</span>
        </NavLink>
        {status === 'signed-in' && (
          <nav className="app-nav" aria-label="Main">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/dashboard">Patterns</NavLink>
            <NavLink to="/transactions">Transactions</NavLink>
            <NavLink to="/receipts/new">+ Add</NavLink>
          </nav>
        )}
        {status === 'signed-in' && (
          <NavLink to="/scan" className="header-scan">
            📷 Scan
          </NavLink>
        )}
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
