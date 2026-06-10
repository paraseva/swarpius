import React from 'react'
import { useGuidance } from './guidanceContext'
import { MarkdownText } from './MarkdownText'
import { useFocusTrap } from '../hooks/useFocusTrap'
import s from './GettingStartedModal.module.css'

export const GETTING_STARTED_ID = 'getting-started'
export const STOP_MARKER_ID = 'stop-marker'

interface GettingStartedModalProps {
  onClose: () => void
  /** Desktop bundle: append the stop-marker setup section + a button that
   *  opens the folder holding the silent track. Omitted/false on
   *  source/Docker, where the setup lives in the repo README instead. */
  isBundle?: boolean
  onOpenStopMarkerFolder?: () => void
}

/**
 * First-run onboarding intro. Renders the shared ``getting-started``
 * guidance section (same source as the contextual `?` help) in a
 * centred, dismissible modal — shown automatically on a pristine
 * install and reopened on demand from the Settings header. On the
 * desktop bundle it also carries the stop-marker setup steps and a
 * one-click "open the folder" button.
 */
export const GettingStartedModal: React.FC<GettingStartedModalProps> = ({
  onClose,
  isBundle = false,
  onOpenStopMarkerFolder,
}) => {
  const guidance = useGuidance()
  const entry = guidance[GETTING_STARTED_ID]
  const stopEntry = guidance[STOP_MARKER_ID]
  const trapRef = useFocusTrap(onClose)
  if (!entry) return null

  return (
    <div
      className={s.backdrop}
      role="dialog"
      aria-modal="true"
      aria-label={entry.title}
    >
      <div ref={trapRef} className={s.card} tabIndex={-1}>
        <header className={s.header}>
          <h2 className={s.title}>{entry.title}</h2>
          <button
            type="button"
            className={s.close}
            onClick={onClose}
            aria-label="Close"
          >
            &times;
          </button>
        </header>
        <div className={s.body}>
          <MarkdownText>{entry.content}</MarkdownText>
          {isBundle && stopEntry && (
            <section>
              <MarkdownText>{`### ${stopEntry.title}\n\n${stopEntry.content}`}</MarkdownText>
              <button
                type="button"
                className={s.secondary}
                onClick={onOpenStopMarkerFolder}
              >
                Open the stop-marker folder
              </button>
            </section>
          )}
        </div>
        <div className={s.actions}>
          <button type="button" className={s.primary} onClick={onClose}>
            Get started
          </button>
        </div>
      </div>
    </div>
  )
}
