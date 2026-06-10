import React from 'react'
import { FocusTrapModal } from './FocusTrapModal'
import { formatVolumePercent, formatZoneLabel } from './zoneStatusUtils'
import type { ZoneArtworkState } from './zoneStatusModel'
import type { ConnectionStatus } from '../websocketContext'
import s from './ZoneStatusPanel.module.css'

interface VolumeModalProps {
  zone: ZoneArtworkState
  draggingVolumes: Record<string, number>
  status: ConnectionStatus
  onDragChange: (outputName: string, value: number) => void
  onCommit: (outputName: string, value: number) => void
  onMute: (outputName: string, mute: boolean) => void
  onClose: () => void
}

export const VolumeModal: React.FC<VolumeModalProps> = ({
  zone, draggingVolumes, status, onDragChange, onCommit, onMute, onClose,
}) => {
  const label = `Volume controls for ${formatZoneLabel(zone.displayName, zone.zoneAlias, zone.groupName)}`
  return (
    <FocusTrapModal
      className={s.zoneVolumeModal}
      onClose={onClose}
      onBackdropClick={onClose}
      label={label}
    >
      <div className={s.zoneVolumeModalContent} onClick={(e) => e.stopPropagation()}>
        <div className={s.zoneVolumeModalHeader}>
          <span className={zone.isGrouped ? s.zoneNameGrouped : ''}>
            Volume — {formatZoneLabel(zone.displayName, zone.zoneAlias, zone.groupName)}
          </span>
          <button
            type="button"
            className={s.zoneVolumeModalClose}
            onClick={onClose}
            aria-label="Close volume controls"
          >&#215;</button>
        </div>
        {zone.outputsVolume.map((vol) => {
          const displayValue = draggingVolumes[vol.name] ?? vol.value ?? 0
          return (
            <div key={vol.name} className={s.zoneVolumeModalRow}>
              <span className={s.zoneVolumeModalName}>{vol.name}</span>
              {vol.type ? (
                <div className={s.zoneVolumeModalControls}>
                  <button
                    type="button"
                    className={s.zoneVolumeMute}
                    onClick={() => onMute(vol.name, !vol.is_muted)}
                    disabled={status !== 'open'}
                    title={vol.is_muted ? 'Unmute' : 'Mute'}
                    aria-label={vol.is_muted ? `Unmute ${vol.name}` : `Mute ${vol.name}`}
                  >
                    {vol.is_muted ? '\u{1F507}' : '\u{1F50A}'}
                  </button>
                  <input
                    type="range"
                    min={vol.min}
                    max={vol.max}
                    step={vol.step}
                    value={displayValue}
                    className={s.zoneVolumeSlider}
                    disabled={status !== 'open'}
                    aria-label={`Volume, ${vol.name}`}
                    aria-valuetext={formatVolumePercent(displayValue, vol.min, vol.max)}
                    onChange={(e) => onDragChange(vol.name, Number(e.target.value))}
                    onMouseUp={(e) => onCommit(vol.name, Number((e.target as HTMLInputElement).value))}
                    onTouchEnd={(e) => onCommit(vol.name, Number((e.target as HTMLInputElement).value))}
                  />
                  <span className={s.zoneVolumeLabel}>{formatVolumePercent(displayValue, vol.min, vol.max)}</span>
                </div>
              ) : (
                <span className={s.zoneVolumeFixed}>Fixed volume</span>
              )}
            </div>
          )
        })}
      </div>
    </FocusTrapModal>
  )
}
