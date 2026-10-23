import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { AccountPage } from './pages/AccountPage'
import { ChatPage } from './pages/ChatPage'
import { ImportPage } from './pages/ImportPage'
import { ReceiptFormPage } from './pages/ReceiptFormPage'
import { ScanPage } from './pages/ScanPage'
import { TransactionsPage } from './pages/TransactionsPage'
import { DashboardSkeleton } from './components/dashboard/DashboardSkeleton'
import './App.css'

// Charts (Recharts) load only when the dashboard is opened.
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))

function App() {
  return (
    <Routes>
      {/* Layout shows the login page to anyone who isn't signed in. */}
      <Route element={<Layout />}>
        <Route
          index
          element={
            <Suspense fallback={<DashboardSkeleton />}>
              <DashboardPage />
            </Suspense>
          }
        />
        {/* The dashboard used to live here; it is the home page now. */}
        <Route path="dashboard" element={<Navigate to="/" replace />} />
        <Route path="ask" element={<ChatPage />} />
        <Route path="transactions" element={<TransactionsPage />} />
        <Route path="scan" element={<ScanPage />} />
        <Route path="receipts/new" element={<ReceiptFormPage />} />
        <Route path="receipts/:receiptId/edit" element={<ReceiptFormPage key="edit" />} />
        <Route path="account" element={<AccountPage />} />
        <Route path="import" element={<ImportPage />} />
        <Route path="*" element={<p className="center">Page not found.</p>} />
      </Route>
    </Routes>
  )
}

export default App
