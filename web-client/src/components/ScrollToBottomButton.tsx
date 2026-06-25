import React from 'react'

interface ScrollToBottomButtonProps {
  show: boolean
  hasNew: boolean
  onClick: () => void
}

/** Transient down-arrow that floats over a scroll container's bottom-right and
 *  jumps it back to the live edge. Highlights itself when new content arrived
 *  below while the user was scrolled up. */
export const ScrollToBottomButton: React.FC<ScrollToBottomButtonProps> = ({
  show, hasNew, onClick,
}) => {
  const label = hasNew ? 'Scroll to new messages' : 'Scroll to bottom'
  return (
    <button
      type="button"
      className={`scroll-to-bottom-button${show ? ' is-visible' : ''}${hasNew ? ' has-new' : ''}`}
      onClick={onClick}
      aria-hidden={!show}
      tabIndex={show ? 0 : -1}
      aria-label={label}
      title={label}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 5v14" />
        <path d="m19 12-7 7-7-7" />
      </svg>
    </button>
  )
}
