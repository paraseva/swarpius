import React from 'react'
import { FocusTrapModal } from './FocusTrapModal'
import type { ZoneArtworkState } from './zoneStatusModel'
import s from './ZoneStatusPanel.module.css'

interface ArtworkOverlayProps {
  zone: ZoneArtworkState
  dataUri: string | null
  visible: boolean
  onClose: () => void
}

export const ArtworkOverlay: React.FC<ArtworkOverlayProps> = ({ zone, dataUri, visible, onClose }) => {
  const label = `Expanded artwork for ${zone.nowPlaying.line1 || zone.displayName}`
  return (
    <FocusTrapModal
      className={`${s.zoneArtworkOverlay} ${visible ? s.zoneArtworkOverlayOpen : s.zoneArtworkOverlayClosed}`}
      onClose={onClose}
      onBackdropClick={onClose}
      label={label}
    >
      {dataUri ? (
        <img src={dataUri} alt={label} className={s.zoneArtworkOverlayImage} />
      ) : (
        <div className={s.zoneArtworkOverlayLoading} aria-live="polite">
          <div className={s.zoneArtworkOverlaySpinner} aria-hidden="true" />
          <span>Loading artwork...</span>
        </div>
      )}
    </FocusTrapModal>
  )
}
