import React from 'react'
import { GuidanceButton } from './GuidanceButton'
import { CloseIcon } from './CloseIcon'
import s from './PanelHeader.module.css'

interface PanelHeaderProps {
  title: string
  /** aria-label for the close button (e.g. "Close Costs"). */
  closeLabel: string
  /** Omit to render no close button. */
  onClose?: () => void
  guidanceId?: string
  guidanceDevMode?: boolean
  children?: React.ReactNode
}

/**
 * The single header for every full-view panel (Costs, Conversation Analysis,
 * Settings, Live Diagnostics) — one implementation so the header and close
 * button are identical everywhere. Title + guidance (?) anchored left, close
 * right; `children` ride the first row (right-aligned when wide, centred when
 * the viewport is narrow).
 */
export const PanelHeader: React.FC<PanelHeaderProps> = ({
  title,
  closeLabel,
  onClose,
  guidanceId,
  guidanceDevMode,
  children,
}) => (
  <header className={s.header}>
    <span className={`panel-heading-group ${s.titleGroup}`}>
      <h3 className={s.title}>{title}</h3>
      {guidanceId ? <GuidanceButton id={guidanceId} isDevMode={guidanceDevMode} /> : null}
    </span>
    {children ? <div className={s.content}>{children}</div> : null}
    {onClose ? (
      <button type="button" className="close-button" onClick={onClose} aria-label={closeLabel}>
        <CloseIcon />
      </button>
    ) : null}
  </header>
)
