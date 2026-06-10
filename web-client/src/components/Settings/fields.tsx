/**
 * Reusable form field components for the Settings tabs.
 *
 * Each tab's content layout (which fields, in what order, with what
 * help text) is unique; these components handle the common patterns
 * (label + input + help line; password masking; provider test status).
 */
import React from 'react'
import f from './fields.module.css'
import { FieldsDisabledContext } from './settingsFormContext'
interface BaseFieldProps {
  id: string
  label: string
  help?: React.ReactNode
  required?: boolean
}

interface TextFieldProps extends BaseFieldProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: 'text' | 'url' | 'number'
  monospace?: boolean
  /** Slot to the left of the input (e.g. a protocol dropdown) */
  leading?: React.ReactNode
  /** Slot to the right of the input (e.g. a Test button) */
  trailing?: React.ReactNode
}

export const TextField: React.FC<TextFieldProps> = ({
  id, label, help, required, value, onChange, placeholder, type = 'text', monospace, leading, trailing,
}) => {
  const disabled = React.useContext(FieldsDisabledContext)
  const input = (
    <input
      id={id}
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={f.input}
      style={monospace ? { fontFamily: 'var(--font-mono)' } : undefined}
      spellCheck={false}
      autoComplete="off"
      disabled={disabled}
    />
  )
  return (
    <div className={f.field}>
      <label htmlFor={id} className={f.label}>
        {label}
        {required ? <span className={f.requiredMark} aria-label="Required">*</span> : null}
      </label>
      {leading || trailing ? (
        <div className={f.inputRow}>
          {leading}
          {input}
          {trailing}
        </div>
      ) : input}
      {help ? <p className={f.help}>{help}</p> : null}
    </div>
  )
}

interface PasswordFieldProps extends BaseFieldProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Slot to the right of the input (e.g. a Test button) */
  trailing?: React.ReactNode
}

export const PasswordField: React.FC<PasswordFieldProps> = ({
  id, label, help, required, value, onChange, placeholder, trailing,
}) => {
  const [shown, setShown] = React.useState(false)
  const disabled = React.useContext(FieldsDisabledContext)
  return (
    <div className={f.field}>
      <label htmlFor={id} className={f.label}>
        {label}
        {required ? <span className={f.requiredMark} aria-label="Required">*</span> : null}
      </label>
      <div className={f.inputRow}>
        <div className={f.passwordWrap}>
          <input
            id={id}
            type={shown ? 'text' : 'password'}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            className={f.input}
            spellCheck={false}
            autoComplete="off"
            style={{ fontFamily: shown ? 'var(--font-mono)' : 'inherit' }}
            disabled={disabled}
          />
          <button
            type="button"
            className={f.passwordToggle}
            onClick={() => setShown((v) => !v)}
            aria-label={shown ? 'Hide value' : 'Show value'}
            title={shown ? 'Hide value' : 'Show value'}
          >
            {shown ? (
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            )}
          </button>
        </div>
        {trailing}
      </div>
      {help ? <p className={f.help}>{help}</p> : null}
    </div>
  )
}

export interface SelectOption {
  value: string
  label: string
}

export interface SelectOptionGroup {
  /** Group label rendered as the <optgroup> label. */
  label: string
  options: SelectOption[]
}

interface SelectFieldProps extends BaseFieldProps {
  value: string
  onChange: (value: string) => void
  /** Either a flat list of options or a list of {label, options}
   * groups for native <optgroup> rendering. */
  options: ReadonlyArray<SelectOption | SelectOptionGroup>
  /** Leading "nothing selected" option (value ""). Without it a native
   * select silently displays its first real option while the bound value
   * is empty, so an unselected field looks chosen. */
  placeholder?: string
}

function isGroup(
  o: SelectOption | SelectOptionGroup,
): o is SelectOptionGroup {
  return Array.isArray((o as SelectOptionGroup).options)
}

export const SelectField: React.FC<SelectFieldProps> = ({
  id, label, help, required, value, onChange, options, placeholder,
}) => {
  const disabled = React.useContext(FieldsDisabledContext)
  return (
  <div className={f.field}>
    <label htmlFor={id} className={f.label}>
      {label}
      {required ? <span className={f.requiredMark} aria-label="Required">*</span> : null}
    </label>
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={f.select}
      disabled={disabled}
    >
      {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
      {options.map((opt, idx) =>
        isGroup(opt) ? (
          <optgroup key={`group-${idx}`} label={opt.label}>
            {opt.options.map((sub) => (
              <option key={sub.value} value={sub.value}>{sub.label}</option>
            ))}
          </optgroup>
        ) : (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ),
      )}
    </select>
    {help ? <p className={f.help}>{help}</p> : null}
  </div>
  )
}

export type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'ok'; detail?: string; notValidated?: boolean }
  | { kind: 'error'; detail?: string }

interface TestConnectionButtonProps {
  state: TestState
  onTest: () => void
  disabled?: boolean
  label?: string
  /** Hover tooltip; falls back to the state's detail when present. */
  title?: string
}

export const TestConnectionButton: React.FC<TestConnectionButtonProps> = ({
  state, onTest, disabled, label = 'Test', title,
}) => {
  const stateClass =
    state.kind === 'ok'
      ? state.notValidated
        ? f.testButtonStateWarning
        : f.testButtonStateOk
      : state.kind === 'error'
      ? f.testButtonStateError
      : ''
  const text =
    state.kind === 'testing'
      ? 'Testing…'
      : state.kind === 'ok'
      ? state.notValidated
        ? 'Saved'
        : 'OK'
      : state.kind === 'error'
      ? 'Error'
      : label
  const hover =
    title
    ?? (state.kind === 'ok' || state.kind === 'error' ? state.detail : undefined)
  return (
    <button
      type="button"
      className={`${f.testButton} ${stateClass}`}
      onClick={onTest}
      disabled={disabled || state.kind === 'testing'}
      title={hover}
    >
      {text}
    </button>
  )
}

interface TestResultProps {
  result: TestState
}

export const TestResult: React.FC<TestResultProps> = ({ result }) => {
  if (result.kind === 'idle' || result.kind === 'testing') return null
  if (result.kind === 'ok') {
    return (
      <p
        className={`${f.testResult} ${
          result.notValidated ? f.testResultWarning : f.testResultOk
        }`}
      >
        {result.detail ?? (result.notValidated ? 'Saved without validation' : 'Connection verified')}
      </p>
    )
  }
  return (
    <p className={`${f.testResult} ${f.testResultError}`}>
      {result.detail ?? 'Test failed'}
    </p>
  )
}

interface ToggleFieldProps extends BaseFieldProps {
  value: boolean
  onChange: (value: boolean) => void
}

/**
 * Toggle switch for boolean settings, matching the visual language of
 * the header TTS toggle. Values are stored in `.env` as the strings
 * `"true"` / `"false"`; the tab is responsible for that conversion.
 */
export const ToggleField: React.FC<ToggleFieldProps> = ({
  id, label, help, required, value, onChange,
}) => {
  const disabled = React.useContext(FieldsDisabledContext)
  return (
  <div className={f.toggleField}>
    <div className={f.toggleRow}>
      <label htmlFor={id} className={f.toggleLabel}>
        {label}
        {required ? <span className={f.requiredMark} aria-label="Required">*</span> : null}
      </label>
      <label className={f.toggleSwitch}>
        <input
          id={id}
          type="checkbox"
          className={f.toggleInput}
          checked={value}
          onChange={(e) => onChange(e.target.checked)}
          disabled={disabled}
        />
        <span className={f.toggleSlider} aria-hidden="true" />
      </label>
    </div>
    {help ? <p className={f.help}>{help}</p> : null}
  </div>
  )
}

interface NumberFieldProps extends BaseFieldProps {
  value: string
  onChange: (value: string) => void
  min?: number
  max?: number
  step?: number
  placeholder?: string
}

/**
 * Number input. We store as a string in form state because `.env`
 * values are strings — the tab can validate / parse before save.
 */
export const NumberField: React.FC<NumberFieldProps> = ({
  id, label, help, required, value, onChange, min, max, step, placeholder,
}) => {
  const disabled = React.useContext(FieldsDisabledContext)
  return (
  <div className={f.field}>
    <label htmlFor={id} className={f.label}>
      {label}
      {required ? <span className={f.requiredMark} aria-label="Required">*</span> : null}
    </label>
    <input
      id={id}
      type="number"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      className={f.input}
      style={{ fontFamily: 'var(--font-mono)' }}
      spellCheck={false}
      autoComplete="off"
      disabled={disabled}
    />
    {help ? <p className={f.help}>{help}</p> : null}
  </div>
  )
}

