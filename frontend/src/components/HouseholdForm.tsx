import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import { errorMessages, getHousehold, saveHousehold, type HouseholdSettings } from '../api/client'

/** Account page: who lives in the household and where, for "Compared with similar households". */
export function HouseholdForm() {
  const [settings, setSettings] = useState<HouseholdSettings | null>(null)
  const [householdType, setHouseholdType] = useState('')
  const [province, setProvince] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const section = useRef<HTMLElement>(null)
  const location = useLocation()

  useEffect(() => {
    getHousehold()
      .then((result) => {
        setSettings(result)
        setHouseholdType(result.household_type)
        setProvince(result.province)
      })
      .catch((error: unknown) => setNotice({ tone: 'error', text: errorMessages(error).join(' ') }))
  }, [])

  // Patterns links here (#household) when the household isn't set yet.
  useEffect(() => {
    if (location.hash === '#household' && settings) section.current?.scrollIntoView({ block: 'start' })
  }, [location.hash, settings])

  const save = async () => {
    setBusy(true)
    setNotice(null)
    try {
      const saved = await saveHousehold(householdType, province)
      setSettings(saved)
      setNotice({ tone: 'ok', text: 'Saved. Patterns now compares your spending with similar households.' })
    } catch (error) {
      setNotice({ tone: 'error', text: errorMessages(error).join(' ') })
    } finally {
      setBusy(false)
    }
  }

  const changed = settings !== null && (householdType !== settings.household_type || province !== settings.province)

  return (
    <section ref={section} id="household" className="card household-form" aria-labelledby="household-title">
      <h2 id="household-title" className="card-title">
        Your household
      </h2>
      <p className="muted">
        Used only to compare your spending with similar households in Patterns (Statistics Canada averages). It's saved in
        your spreadsheet.
      </p>
      {settings && (
        <>
          <label>
            Who lives with you
            <select value={householdType} onChange={(event) => setHouseholdType(event.target.value)}>
              <option value="" disabled>
                Choose…
              </option>
              {settings.household_types.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Where you live
            <select value={province} onChange={(event) => setProvince(event.target.value)}>
              <option value="" disabled>
                Choose…
              </option>
              {settings.provinces.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="button button-primary"
            onClick={() => void save()}
            disabled={busy || !householdType || !province || !changed}
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      )}
      {notice && (
        <p className={`notice notice-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}
    </section>
  )
}
