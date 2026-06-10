import React from 'react'
import Markdown from 'react-markdown'
import { useFocusTrap } from '../hooks/useFocusTrap'
import type { GuidanceEntry } from '../utils/parseGuidanceSections'
import s from './Guidance.module.css'

const VIEWPORT_PADDING = 12

interface GuidancePopoverProps {
  entry: GuidanceEntry
  anchorRect: DOMRect
  anchorElement: HTMLElement
  onClose: () => void
}

export const GuidancePopover: React.FC<GuidancePopoverProps> = ({ entry, anchorRect, anchorElement, onClose }) => {
  const trapRef = useFocusTrap(onClose)
  const [position, setPosition] = React.useState<React.CSSProperties>({
    position: 'fixed',
    top: anchorRect.bottom + 8,
    left: anchorRect.left + anchorRect.width / 2,
    transform: 'translateX(-50%)',
    visibility: 'hidden',
  })

  // Measure after first render and clamp to viewport
  React.useLayoutEffect(() => {
    const el = trapRef.current
    if (!el) return

    const popoverWidth = el.offsetWidth
    const popoverHeight = el.offsetHeight
    const idealLeft = anchorRect.left + anchorRect.width / 2 - popoverWidth / 2
    const idealTop = anchorRect.bottom + 8

    let left = idealLeft
    let top = idealTop

    // Clamp horizontal
    if (left + popoverWidth > window.innerWidth - VIEWPORT_PADDING) {
      left = window.innerWidth - VIEWPORT_PADDING - popoverWidth
    }
    if (left < VIEWPORT_PADDING) {
      left = VIEWPORT_PADDING
    }

    // If it overflows the bottom, position above the anchor
    if (top + popoverHeight > window.innerHeight - VIEWPORT_PADDING) {
      top = anchorRect.top - 8 - popoverHeight
    }

    setPosition({
      position: 'fixed',
      top,
      left,
      visibility: 'visible',
    })
  }, [anchorRect, trapRef])

  // Close on click outside (but not on the anchor button — that handles its own toggle)
  React.useEffect(() => {
    const handleMouseDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (trapRef.current && !trapRef.current.contains(target) && !anchorElement.contains(target)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [onClose, trapRef, anchorElement])

  return (
    <div
      ref={trapRef}
      className={s.popover}
      style={position}
      role="dialog"
      aria-label={entry.title}
      tabIndex={-1}
    >
      <div className={s.popoverHeader}>
        <h4 className={s.popoverTitle}>{entry.title}</h4>
        <button
          type="button"
          className={s.popoverClose}
          onClick={onClose}
          aria-label="Close"
        >
          &times;
        </button>
      </div>
      <div className={s.popoverBody}>
        <Markdown>{entry.content}</Markdown>
      </div>
    </div>
  )
}
