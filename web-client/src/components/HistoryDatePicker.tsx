import React from 'react'

function toIsoDate(ms: number): string {
  const d = new Date(ms)
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

/**
 * A calendar icon that opens the browser's native date picker (full-screen on
 * mobile) and reports the chosen local day's start. The hidden input is kept in
 * the layout (not display:none) so showPicker() works; older browsers fall back
 * to focusing it.
 */
export const HistoryDatePicker: React.FC<{ onPick: (dayStartMs: number) => void }> = ({ onPick }) => {
  const inputRef = React.useRef<HTMLInputElement>(null)
  const [maxDate] = React.useState(() => toIsoDate(Date.now()))

  const open = () => {
    const el = inputRef.current
    if (!el) return
    if (typeof el.showPicker === 'function') {
      try {
        el.showPicker()
        return
      } catch {
        // fall through to focus-based fallback
      }
    }
    el.focus()
    el.click()
  }

  const onChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    if (!value) return
    const [y, m, d] = value.split('-').map(Number)
    onPick(new Date(y, m - 1, d).getTime())
  }

  return (
    <span className="history-date-picker">
      <button
        type="button"
        className="history-date-button"
        onClick={open}
        title="Jump to a date"
        aria-label="Jump to a date"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      </button>
      <input
        ref={inputRef}
        type="date"
        max={maxDate}
        onChange={onChange}
        className="history-date-input"
        tabIndex={-1}
        aria-hidden="true"
      />
    </span>
  )
}
