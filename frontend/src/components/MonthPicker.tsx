import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'

const monthName = new Intl.DateTimeFormat(undefined, { month: 'short' })
const MONTHS = Array.from({ length: 12 }, (_, i) => monthName.format(new Date(2000, i, 1)))

/** Year arrows and a grid of months, in a dialog. The native month input can't change the year
 * in Android's WebView, so the app uses its own picker on every platform. */
export function MonthPicker({
  value,
  max,
  onChange,
  onClose,
}: {
  value: string
  max: string
  onChange: (month: string) => void
  onClose: () => void
}) {
  const maxYear = Number(max.slice(0, 4))
  const [year, setYear] = useState(() => Number((value || max).slice(0, 4)))
  const dialog = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    dialog.current?.querySelector<HTMLButtonElement>('.month-cell-on, .month-cell:not(:disabled)')?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // In document.body, so the page's stacking context can't put the header above the dialog.
  return createPortal(
    <div className="month-picker-backdrop" onClick={onClose}>
      <div
        ref={dialog}
        className="month-picker"
        role="dialog"
        aria-modal="true"
        aria-label="Pick a month"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="month-picker-year">
          <button type="button" className="icon-button" onClick={() => setYear(year - 1)} aria-label="Previous year">
            <Icon name="left" size={20} />
          </button>
          <span aria-live="polite">{year}</span>
          <button
            type="button"
            className="icon-button"
            onClick={() => setYear(year + 1)}
            disabled={year >= maxYear}
            aria-label="Next year"
          >
            <Icon name="right" size={20} />
          </button>
        </div>
        <div className="month-picker-grid">
          {MONTHS.map((name, index) => {
            const month = `${year}-${String(index + 1).padStart(2, '0')}`
            return (
              <button
                key={month}
                type="button"
                className={`month-cell${month === value ? ' month-cell-on' : ''}`}
                aria-pressed={month === value}
                disabled={month > max}
                onClick={() => {
                  onChange(month)
                  onClose()
                }}
              >
                {name}
              </button>
            )
          })}
        </div>
      </div>
    </div>,
    document.body,
  )
}
