import React from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'

/** Wrapper that provides focus trapping for modal dialogs. */
export const FocusTrapModal: React.FC<{
  className: string
  onClose: () => void
  onBackdropClick: () => void
  label: string
  children: React.ReactNode
}> = ({ className, onClose, onBackdropClick, label, children }) => {
  const trapRef = useFocusTrap(onClose)
  return (
    <div
      className={className}
      onClick={onBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      ref={trapRef}
      tabIndex={-1}
    >
      {children}
    </div>
  )
}
