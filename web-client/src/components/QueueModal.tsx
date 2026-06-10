import React from 'react'
import { FocusTrapModal } from './FocusTrapModal'
import { formatDuration, formatZoneLabel } from './zoneStatusUtils'
import type { QueueItem, ZoneArtworkState } from './zoneStatusModel'
import type { ConnectionStatus } from '../websocketContext'
import s from './ZoneStatusPanel.module.css'

interface QueueModalProps {
  zone: ZoneArtworkState
  items: QueueItem[]
  timeRemaining?: number
  status: ConnectionStatus
  onPlayFromHere: (zoneDisplayName: string, queueItemId: number) => void
  onClose: () => void
}

export const QueueModal: React.FC<QueueModalProps> = ({
  zone, items, timeRemaining, status, onPlayFromHere, onClose,
}) => {
  const timeLabel = timeRemaining != null ? `, ${formatDuration(timeRemaining)} remaining` : ''
  return (
    <FocusTrapModal
      className={s.zoneQueueModal}
      onClose={onClose}
      onBackdropClick={onClose}
      label={`Queue for ${formatZoneLabel(zone.displayName, zone.zoneAlias, zone.groupName)}`}
    >
      <div className={s.zoneQueueModalContent} onClick={(e) => e.stopPropagation()}>
        <div className={s.zoneQueueModalHeader}>
          <span className={zone.isGrouped ? s.zoneNameGrouped : ''}>
            Queue — {formatZoneLabel(zone.displayName, zone.zoneAlias, zone.groupName)} ({items.length} tracks{timeLabel})
          </span>
          <button
            type="button"
            className={s.zoneVolumeModalClose}
            onClick={onClose}
            aria-label="Close queue"
          >&#215;</button>
        </div>
        <div className={s.zoneQueueModalList}>
          {items.map((item, idx) => {
            const title = item.two_line?.line1 ?? item.one_line?.line1 ?? '—'
            const artist = item.two_line?.line2 ?? ''
            const isNowPlaying = idx === 0
            return (
              <div
                key={item.queue_item_id}
                className={`${s.zoneQueueModalRow} ${isNowPlaying ? s.zoneQueueNowPlaying : ''}`}
              >
                <span className={s.zoneQueuePosition}>{idx + 1}.</span>
                {isNowPlaying && <span className={s.zoneQueuePlayingIcon}>&#9654;</span>}
                <span className={s.zoneQueueTrackInfo}>
                  <span className={s.zoneQueueTitle}>{title}</span>
                  {artist && <span className={s.zoneQueueArtist}>{artist}</span>}
                </span>
                <span className={s.zoneQueueDuration}>{formatDuration(item.length)}</span>
                <button
                  type="button"
                  className={s.zoneQueuePlayButton}
                  onClick={() => onPlayFromHere(zone.displayName, item.queue_item_id)}
                  disabled={status !== 'open'}
                  title="Play from here"
                  aria-label={`Play from ${title}`}
                >
                  &#9654;
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </FocusTrapModal>
  )
}
