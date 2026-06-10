import React from 'react'
import s from './TtsToggle.module.css'
import { GuidanceButton } from './GuidanceButton'
import { TtsStatusIndicator, type TtsIndicatorPhase } from './TtsStatusIndicator'

interface TtsToggleProps {
  enabled: boolean
  onChange: (enabled: boolean) => void
  disabled?: boolean
  notConfigured?: boolean
  onTestTts?: () => void
  testTtsPhase?: TtsIndicatorPhase | null
  /** TTS reachability state. `'failing'` tints the icon red so the
   *  user knows auto-TTS is silently suppressed pending a successful
   *  test-button click. */
  health?: 'checking' | 'healthy' | 'failing'
}

export const TtsToggle: React.FC<TtsToggleProps> = ({
  enabled, onChange, disabled = false, notConfigured = false,
  onTestTts, testTtsPhase = null, health = 'healthy',
}) => {
  const isFailing = health === 'failing' && !disabled
  const tooltipText = disabled
    ? 'TTS not configured'
    : isFailing
      ? 'TTS unreachable — click to retry'
      : 'Test TTS'
  // When the server is unreachable, render the toggle as off + disabled
  // without mutating the caller's preference — parent keeps `enabled`
  // intact so the user's choice is restored automatically once health
  // flips back to 'healthy'.
  const toggleDisabled = disabled || isFailing
  const toggleChecked = enabled && !toggleDisabled
  const toggleTooltip = disabled
    ? 'TTS not configured'
    : isFailing
      ? 'No TTS server'
      : undefined
  return (
    <div className={s.group}>
      <GuidanceButton id="tts" />
      <button
        type="button"
        className={
          `${s.label}` +
          `${disabled ? ` ${s.labelDisabled}` : ''}` +
          `${isFailing ? ` ${s.labelFailing}` : ''}`
        }
        onClick={onTestTts}
        disabled={disabled || testTtsPhase !== null}
        title={tooltipText}
        aria-label="Text-to-Speech"
      >
        {testTtsPhase ? (
          <TtsStatusIndicator phase={testTtsPhase} />
        ) : (
          <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
            <path d="M4 22q-.825 0-1.412-.587T2 20V4q0-.825.588-1.412T4 2h8.15l-2 2H4v16h11v-2h2v2q0 .825-.587 1.413T15 22zm2-4v-2h7v2zm0-3v-2h5v2zm9 0l-4-4H8V6h3l4-4zm2-3.05v-6.9q.9.525 1.45 1.425T19 8.5t-.55 2.025T17 11.95m0 4.3v-2.1q1.75-.625 2.875-2.162T21 8.5t-1.125-3.488T17 2.85V.75q2.6.675 4.3 2.813T23 8.5t-1.7 4.938T17 16.25" />
          </svg>
        )}
      </button>
      <label
        className={`${s.toggle}${toggleDisabled ? ` ${s.disabled}` : ''}`}
        htmlFor="tts-toggle-input"
        title={toggleTooltip}
      >
        <input
          id="tts-toggle-input"
          className={s.input}
          type="checkbox"
          checked={toggleChecked}
          onChange={(event) => onChange(event.target.checked)}
          disabled={toggleDisabled}
        />
        <span className={s.switch} aria-hidden="true" />
        <span className={s.state}>{toggleChecked ? 'On' : 'Off'}</span>
      </label>
      {notConfigured ? <span className={s.note}>Not Configured</span> : null}
    </div>
  )
}
