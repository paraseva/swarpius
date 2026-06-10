import React from 'react'
import s from './DefaultZoneBadge.module.css'
import { createUuid } from '../utils/uuid'
import { useWebSocket } from '../websocketContext'
import { parseJson } from '../utils/parseJson'
import { formatZoneLabel } from './zoneStatusUtils'
import { buildDropdownEntries, isDefaultOffline, type ZoneOption } from './defaultZoneDropdown'

export interface DefaultZoneInfo {
  zone_name: string | null
  alias: string | null
  group_name: string | null
  is_grouped: boolean
  is_online: boolean
}

interface DefaultZoneBadgeProps {
  zone: DefaultZoneInfo | null
}

const STATE_DOT_CLASS: Record<string, string> = {
  playing: 'dotPlaying',
  paused: 'dotPaused',
  offline: 'dotOffline',
}

const displayLabel = (z: ZoneOption): string =>
  formatZoneLabel(z.display_name, z.zone_alias, z.group_name)

export const DefaultZoneBadge: React.FC<DefaultZoneBadgeProps> = ({ zone }) => {
  const { messages, sendMessage, trimmedCount } = useWebSocket()

  const [isOpen, setIsOpen] = React.useState(false)
  const [zones, setZones] = React.useState<ZoneOption[] | null>(null)
  const [pendingZone, setPendingZone] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const listRequestId = React.useRef<string | null>(null)
  const setRequestId = React.useRef<string | null>(null)
  const dropdownRef = React.useRef<HTMLDivElement>(null)
  const triggerRef = React.useRef<HTMLButtonElement>(null)
  const optionListRef = React.useRef<HTMLDivElement>(null)

  // Watch for roon-control-response messages matching our request IDs
  const processedRef = React.useRef(0)
  React.useEffect(() => {
    const relativeIdx = Math.max(0, processedRef.current - trimmedCount)
    const nextMessages = messages.slice(relativeIdx)
    processedRef.current = messages.length + trimmedCount

    for (const msg of nextMessages) {
      if (msg.direction !== 'inbound' || msg.channel !== 'roon-control-response') continue
      const payload = parseJson<Record<string, unknown>>(msg.payload ?? msg.body)
      if (!payload) continue
      const reqId = payload.request_id as string | undefined

      if (reqId === listRequestId.current) {
        listRequestId.current = null
        if (payload.ok && Array.isArray(payload.zones)) {
          setZones(payload.zones as ZoneOption[])
          setError(null)
        } else {
          setError(String(payload.error ?? 'Failed to load zones'))
        }
      }

      if (reqId === setRequestId.current) {
        setRequestId.current = null
        if (payload.ok) {
          // Success — badge will update via default-zone-update broadcast.
          setIsOpen(false)
          setPendingZone(null)
          setError(null)
        } else {
          setError(String(payload.error ?? 'Failed to set default zone'))
          setPendingZone(null)
          const failed = typeof payload.zone === 'string' ? payload.zone : null
          if (failed) {
            setZones((prev) => prev?.filter((z) => z.display_name !== failed) ?? null)
          }
        }
      }
    }
  }, [messages, trimmedCount])

  const openDropdown = () => {
    setIsOpen(true)
    setZones(null)
    setError(null)
    setPendingZone(null)
    const reqId = createUuid()
    listRequestId.current = reqId
    sendMessage('roon-control-request', JSON.stringify({ request_id: reqId, action: 'list_zones' }))
  }

  const selectZone = (displayName: string) => {
    if (pendingZone) return
    setPendingZone(displayName)
    setError(null)
    const reqId = createUuid()
    setRequestId.current = reqId
    sendMessage('roon-control-request', JSON.stringify({ request_id: reqId, action: 'set_default_zone', zone: displayName }))
  }

  const closeDropdown = () => {
    setIsOpen(false)
    setZones(null)
    setError(null)
    setPendingZone(null)
    listRequestId.current = null
    setRequestId.current = null
  }

  // Focus first option when zones load
  React.useEffect(() => {
    if (!isOpen || !zones?.length) return
    const first = optionListRef.current?.querySelector<HTMLButtonElement>('button:not([disabled])')
    first?.focus()
  }, [isOpen, zones])

  // Click outside to close, Escape to close, arrow key navigation
  React.useEffect(() => {
    if (!isOpen) return
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        closeDropdown()
        triggerRef.current?.focus()
      }
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closeDropdown()
        triggerRef.current?.focus()
        return
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const options = optionListRef.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])')
        if (!options?.length) return
        const current = Array.from(options).indexOf(document.activeElement as HTMLButtonElement)
        let next: number
        if (e.key === 'ArrowDown') {
          next = current < options.length - 1 ? current + 1 : 0
        } else {
          next = current > 0 ? current - 1 : options.length - 1
        }
        options[next].focus()
      }
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [isOpen])

  if (!zone?.zone_name) return null

  const label = formatZoneLabel(zone.zone_name!, zone.alias, zone.group_name)
  const offline = isDefaultOffline(zone)
  const renderedZones = buildDropdownEntries(zone, zones ?? [])

  return (
    <div className={s.badge} ref={dropdownRef}>
      <button
        type="button"
        ref={triggerRef}
        className={`${s.badgeButton}${offline ? ` ${s.badgeButtonOffline}` : ''}`}
        onClick={isOpen ? closeDropdown : openDropdown}
        title={offline ? `Default zone ${label} is currently offline — click to change` : 'Click to change default zone'}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        <span className={s.label}>Default Zone</span>
        <span className={`${s.name}${zone.is_grouped ? ` ${s.nameGrouped}` : ''}${offline ? ` ${s.nameOffline}` : ''}`}>{label}</span>
        <svg className={`${s.chevron}${isOpen ? ` ${s.chevronOpen}` : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {isOpen && (
        <div className={s.dropdown} ref={optionListRef} role="listbox" aria-label="Available zones">
          {zones === null && !error && (
            <div className={s.loading}>Loading zones...</div>
          )}
          {zones !== null && renderedZones.length === 0 && (
            <div className={s.loading}>No zones available</div>
          )}
          {zones !== null && renderedZones.map((z) => {
            const isPending = pendingZone === z.display_name
            const isOffline = z.state === 'offline'
            const dotClass = s[STATE_DOT_CLASS[z.state] ?? 'dotStopped'] as string
            return (
              <button
                key={z.display_name}
                type="button"
                role="option"
                aria-selected={z.is_default}
                className={`${s.option}${z.is_default ? ` ${s.optionCurrent}` : ''}${isPending ? ` ${s.optionPending}` : ''}${isOffline ? ` ${s.optionOffline}` : ''}`}
                onClick={() => selectZone(z.display_name)}
                disabled={!!pendingZone || z.is_default}
                title={
                  isOffline ? `${displayLabel(z)} is currently offline`
                    : z.is_grouped ? `Group: ${z.group_members.join(', ')}`
                    : undefined
                }
              >
                <span className={`${s.dot} ${dotClass}${isPending ? ` ${s.dotPending}` : ''}`} aria-hidden="true" />
                <span className={`${s.optionLabel}${z.is_grouped ? ` ${s.nameGrouped}` : ''}`}>{displayLabel(z)}</span>
                {z.is_grouped && (
                  <span className={s.groupBadge}>{z.group_members.length} zones</span>
                )}
                {z.is_default && <span className={s.check}>&#10003;</span>}
              </button>
            )
          })}
          {error && (
            <div className={s.error}>{error}</div>
          )}
        </div>
      )}
    </div>
  )
}
