import { formatMonth } from '../lib/format'
import { openPicker } from '../lib/picker'
import { Icon } from './Icon'

function shiftMonth(month: string, delta: number): string {
  const [year, number] = month.split('-').map(Number)
  const index = year * 12 + (number - 1) + delta
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`
}

/** ‹ October 2026 › with the native month picker behind the label. */
export function MonthSwitch({ month, max, onChange }: { month: string; max: string; onChange: (month: string) => void }) {
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
      <label className="month-switch-label">
        <span>{formatMonth(month)}</span>
        <Icon name="down" size={16} />
        <input
          type="month"
          className="month-switch-input"
          aria-label="Month"
          value={month}
          max={max}
          onClick={openPicker}
          onChange={(event) => event.target.value && onChange(event.target.value)}
        />
      </label>
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
