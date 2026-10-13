import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { RequireSignIn } from './components/RequireSignIn'
import { HomePage } from './pages/HomePage'
import { ReceiptFormPage } from './pages/ReceiptFormPage'
import { ScanPage } from './pages/ScanPage'
import { TransactionsPage } from './pages/TransactionsPage'
import './App.css'

// Charts (Recharts) load only when the dashboard is opened.
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route
          path="dashboard"
          element={
            <RequireSignIn>
              <Suspense fallback={<p className="muted center">Loading…</p>}>
                <DashboardPage />
              </Suspense>
            </RequireSignIn>
          }
        />
        <Route
          path="transactions"
          element={
            <RequireSignIn>
              <TransactionsPage />
            </RequireSignIn>
          }
        />
        <Route
          path="scan"
          element={
            <RequireSignIn>
              <ScanPage />
            </RequireSignIn>
          }
        />
        <Route
          path="receipts/new"
          element={
            <RequireSignIn>
              <ReceiptFormPage />
            </RequireSignIn>
          }
        />
        <Route
          path="receipts/:receiptId/edit"
          element={
            <RequireSignIn>
              <ReceiptFormPage key="edit" />
            </RequireSignIn>
          }
        />
        <Route path="*" element={<p className="center">Page not found.</p>} />
      </Route>
    </Routes>
  )
}

export default App
