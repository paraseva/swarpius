import React from 'react'
import { createPortal } from 'react-dom'
import { useGuidance } from './guidanceContext'
import { GuidancePopover } from './GuidancePopover'
import s from './Guidance.module.css'

interface GuidanceButtonProps {
  id: string
  devOnly?: boolean
  isDevMode?: boolean
}

export const GuidanceButton: React.FC<GuidanceButtonProps> = ({ id, devOnly = false, isDevMode = false }) => {
  const sections = useGuidance()
  const [anchor, setAnchor] = React.useState<{ rect: DOMRect; element: HTMLElement } | null>(null)
  const buttonRef = React.useRef<HTMLButtonElement>(null)

  const entry = sections[id]
  const isOpen = anchor !== null

  // Don't render if: section doesn't exist, or it's dev-only and we're not in dev mode
  if (!entry) return null
  if ((devOnly || entry.devOnly) && !isDevMode) return null

  const handleClick = () => {
    setAnchor((prev) => {
      if (prev) return null
      if (!buttonRef.current) return null
      return { rect: buttonRef.current.getBoundingClientRect(), element: buttonRef.current }
    })
  }

  const handleClose = () => {
    setAnchor(null)
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={s.guidanceButton}
        onClick={handleClick}
        aria-expanded={isOpen}
        aria-label={`Help: ${entry.title}`}
        title={entry.title}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={s.guidanceIcon}>
          <circle cx="12" cy="12" r="10" />
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      </button>
      {anchor
        ? createPortal(
            <GuidancePopover
              entry={entry}
              anchorRect={anchor.rect}
              anchorElement={anchor.element}
              onClose={handleClose}
            />,
            document.body,
          )
        : null}
    </>
  )
}
