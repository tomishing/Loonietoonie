import { useEffect, useState } from 'react'
import { getHealth } from './api/client'
import './App.css'

type BackendStatus = 'checking' | 'online' | 'offline'

function App() {
  const [status, setStatus] = useState<BackendStatus>('checking')
  const [version, setVersion] = useState<string>('')

  useEffect(() => {
    let active = true
    getHealth()
      .then((health) => {
        if (!active) return
        setStatus('online')
        setVersion(health.version)
      })
      .catch(() => {
        if (active) setStatus('offline')
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <main className="home">
      <img className="mascot" src="/favicon.svg" alt="LoonieToonie mascot" width={120} height={120} />
      <h1>LoonieToonie</h1>
      <p className="tagline">Scan receipts, track spending, and see how much you can save.</p>
      <p className={`status status-${status}`}>
        Backend: {status}
        {status === 'online' && version ? ` (v${version})` : ''}
      </p>
    </main>
  )
}

export default App
