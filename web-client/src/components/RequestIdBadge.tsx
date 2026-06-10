import React from 'react'
import s from './RequestIdBadge.module.css'

interface RequestIdBadgeProps {
  requestId: string
}

export const RequestIdBadge: React.FC<RequestIdBadgeProps> = ({ requestId }) => {
  const [copied, setCopied] = React.useState(false)

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

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    copyToClipboard()
  }

  return (
    <span
      className={`${s.badge}${copied ? ` ${s.copied}` : ''}`}
      onClick={handleClick}
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
