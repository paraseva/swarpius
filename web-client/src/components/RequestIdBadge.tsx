import React from 'react'
import s from './RequestIdBadge.module.css'
import { useRequestFocus } from '../requestFocusContext'

interface RequestIdBadgeProps {
  requestId: string
  /** When set, clicking the id focuses this request across all open
   *  request-aware panels (this panel is the source and stays put). Identifies
   *  the source panel. Omit on non-sync surfaces (e.g. analysis views) — the
   *  badge is then copy-only. */
  syncKey?: string
  /** Local day (YYYY-MM-DD) of this request, so the focus targets the right one
   *  when the same id recurs on other days. */
  day?: string | null
}

const fallbackCopy = (text: string): boolean => {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    // ignore
  }
  document.body.removeChild(textarea)
  return ok
}

export const RequestIdBadge: React.FC<RequestIdBadgeProps> = ({ requestId, syncKey, day }) => {
  const [copied, setCopied] = React.useState(false)
  const focus = useRequestFocus()
  const canSync = syncKey != null && focus != null

  const copyToClipboard = () => {
    const onSuccess = () => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(requestId).then(onSuccess).catch(() => {
        // Clipboard API unavailable (non-HTTPS) — use execCommand fallback
        if (fallbackCopy(requestId)) onSuccess()
      })
    } else {
      if (fallbackCopy(requestId)) onSuccess()
    }
  }

  // Without sync, the whole badge copies (original behaviour).
  if (!canSync) {
    return (
      <span
        className={`${s.badge}${copied ? ` ${s.copied}` : ''}`}
        onClick={(e) => { e.stopPropagation(); copyToClipboard() }}
        title={copied ? 'Copied!' : `Click to copy ${requestId}`}
        role="button"
        tabIndex={0}
        aria-label={`Copy request ID ${requestId}`}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            e.stopPropagation()
            copyToClipboard()
          }
        }}
      >
        <span aria-live="polite">{copied ? 'copied!' : requestId}</span>
      </span>
    )
  }

  // With sync, the id focuses the request elsewhere; a small icon copies. On
  // copy, only the id text becomes a green "copied!" (the id slot keeps its
  // width and the copy icon stays put).
  const doFocus = (e: React.SyntheticEvent) => {
    e.stopPropagation()
    focus!.focusRequest(requestId, syncKey!, day)
  }

  return (
    <span className={`${s.badge} ${s.badgeSync}${copied ? ` ${s.copied}` : ''}`}>
      <span
        className={s.badgeId}
        onClick={doFocus}
        title={`Show request ${requestId} in the other panels`}
        role="button"
        tabIndex={0}
        aria-label={`Show request ${requestId} in other panels`}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            doFocus(e)
          }
        }}
      >
        <span aria-live="polite">{copied ? 'copied!' : requestId}</span>
      </span>
      <button
        type="button"
        className={s.badgeCopy}
        onClick={(e) => { e.stopPropagation(); copyToClipboard() }}
        title="Copy request ID"
        aria-label={`Copy request ID ${requestId}`}
      >
        ⧉
      </button>
    </span>
  )
}
