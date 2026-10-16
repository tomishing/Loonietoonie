import { useState } from 'react'
import { formatMonth } from '../lib/format'
import { Icon } from './Icon'
import { MonthPicker } from './MonthPicker'

function shiftMonth(month: string, delta: number): string {
  const [year, number] = month.split('-').map(Number)
  const index = year * 12 + (number - 1) + delta
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`
}

/** ‹ October 2026 ›; the label opens the month picker. */
export function MonthSwitch({ month, max, onChange }: { month: string; max: string; onChange: (month: string) => void }) {
  const [picking, setPicking] = useState(false)
  return (
    <div className="month-switch" role="group" aria-label="Month">
      <button
        type="button"
        className="icon-button"
        onClick={() => onChange(shiftMonth(month, -1))}
        aria-label="Previous month"
      >
        <Icon name="left" size={20} />
      </button>
      <button
        type="button"
        className="month-switch-label"
        aria-haspopup="dialog"
        aria-label={`Month: ${formatMonth(month)}`}
        onClick={() => setPicking(true)}
      >
        <span>{formatMonth(month)}</span>
        <Icon name="down" size={16} />
      </button>
      {picking && <MonthPicker value={month} max={max} onChange={onChange} onClose={() => setPicking(false)} />}
      <button
        type="button"
        className="icon-button"
        onClick={() => onChange(shiftMonth(month, 1))}
        disabled={month >= max}
        aria-label="Next month"
      >
        <Icon name="right" size={20} />
      </button>
    </div>
  )
}
