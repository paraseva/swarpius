import React from 'react'
import { GuidanceButton } from './GuidanceButton'
import { useWebSocket } from '../websocketContext'
import {
  type QueueItem,
  type QueueUpdatePayload,
  type RoonControlResponsePayload,
  type SnapshotZone,
  type ZoneArtworkState,
  type ZoneSnapshotEvent,
  ARTWORK_PENDING_TIMEOUT_MS,
  DEFAULT_ART_HEIGHT,
  DEFAULT_ART_WIDTH,
  FINISHED_ZONE_IDLE_SECONDS,
} from './zoneStatusModel'
import { formatDuration, formatVolumePercent, formatZoneLabel, imageCacheKey, parseJson } from './zoneStatusUtils'
import { ArtworkOverlay } from './ArtworkOverlay'
import { stopButtonTooltip } from './stopButtonTooltip'
import { QueueModal } from './QueueModal'
import { VolumeModal } from './VolumeModal'
import { useRoonCommands } from '../hooks/useRoonCommands'
import { useZoneImageCache } from '../hooks/useZoneImageCache'
import s from './ZoneStatusPanel.module.css'

const MAX_FULLSCREEN_ART_DIMENSION = 2048
const ARTWORK_OVERLAY_ANIMATION_MS = 220

const ZONE_STATE_CLASS: Record<string, string> = {
  playing: s.zoneStatePlaying,
  paused: s.zoneStatePaused,
  stopped: s.zoneStateStopped,
  unknown: s.zoneStateStopped,
}

const artworkPlaceholderText = (zone: ZoneArtworkState, now: number): string => {
  if (zone.state === 'stopped') return 'Not playing'
  if (zone.imageKey === null) return 'No artwork'
  if (zone.imageLoadFailed) return 'Cannot load image'
  const startedAt = zone.imageRequestStartedAt ?? 0
  if (startedAt > 0 && now - startedAt > ARTWORK_PENDING_TIMEOUT_MS) {
    return 'Cannot load image'
  }
  return 'Loading...'
}

interface PendingExpandRequest {
  zoneId: string
  cacheKey: string
}

const isSameNowPlaying = (a: ZoneArtworkState['nowPlaying'], b: ZoneArtworkState['nowPlaying']): boolean =>
  (a.line1 ?? '') === (b.line1 ?? '') &&
  (a.line2 ?? '') === (b.line2 ?? '') &&
  (a.line3 ?? '') === (b.line3 ?? '') &&
  Number(a.length ?? 0) === Number(b.length ?? 0)

const isSameVolume = (
  a: ZoneArtworkState['outputsVolume'],
  b: ZoneArtworkState['outputsVolume'],
): boolean => {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i].name !== b[i].name) return false
    if (a[i].value !== b[i].value) return false
    if (a[i].is_muted !== b[i].is_muted) return false
  }
  return true
}

function isStopMarker(
  nowPlaying: SnapshotZone['now_playing'] | undefined,
  markerTitle: string,
): boolean {
  if (!markerTitle || !nowPlaying) return false
  return nowPlaying.line1 === markerTitle
}

function mergeSnapshotZone(
  incoming: SnapshotZone,
  existing: ZoneArtworkState | undefined,
  timestamp: number,
  imageCache: ReturnType<typeof useZoneImageCache>,
  markerTitle: string,
): ZoneArtworkState {
  const zoneId = incoming.zone_id ?? ''
  // Marker filter: rewrite state=stopped, clear image, preserve
  // existing nowPlaying so the card doesn't thrash during the ~0.5s
  // the silent marker track plays.
  const markerHit = isStopMarker(incoming.now_playing, markerTitle)
  const incomingState = markerHit
    ? 'stopped'
    : (incoming.state ?? 'unknown').toLowerCase()
  const incomingImageKey = markerHit ? null : (incoming.image_key ?? null)
  const incomingNowPlaying: ZoneArtworkState['nowPlaying'] = markerHit
    ? (existing?.nowPlaying ?? {})
    : (incoming.now_playing ?? {})
  const imageKeyChanged = (existing?.imageKey ?? null) !== incomingImageKey

  let artDataUri: string | undefined = existing?.artDataUri
  if (markerHit) {
    artDataUri = undefined
  } else if (incomingImageKey) {
    const cached = imageCache.lookup(incomingImageKey, DEFAULT_ART_WIDTH, DEFAULT_ART_HEIGHT)
    if (imageKeyChanged) {
      artDataUri = cached
    } else if (!artDataUri && cached) {
      artDataUri = cached
    }
  } else {
    artDataUri = undefined
  }

  // stoppedSince anchors the "finished" grace window. 0 (= past)
  // when the zone arrives already stopped → filter excludes it
  // immediately; "now" when transitioning into stopped → 2s grace.
  let stoppedSince: number | undefined
  if (incomingState !== 'stopped') {
    stoppedSince = undefined
  } else if (!existing) {
    stoppedSince = 0
  } else if (existing.state === 'stopped') {
    stoppedSince = existing.stoppedSince
  } else {
    stoppedSince = Date.now() / 1000
  }

  return {
    zoneId,
    displayName: incoming.display_name ?? existing?.displayName ?? zoneId,
    zoneAlias: incoming.zone_alias ?? null,
    groupName: incoming.group_name ?? null,
    state: incomingState,
    seekPosition: Number(incoming.seek_position ?? 0),
    queueTimeRemaining: incoming.queue_time_remaining,
    imageKey: incomingImageKey,
    nowPlaying: incomingNowPlaying,
    lastUpdatedAt: timestamp,
    artDataUri,
    isGrouped: incoming.is_grouped ?? false,
    groupMembers: incoming.group_members ?? [],
    outputsVolume: incoming.outputs_volume ?? [],
    shuffle: incoming.shuffle ?? false,
    loop: incoming.loop ?? 'disabled',
    autoRadio: incoming.auto_radio ?? false,
    imageLoadFailed: imageKeyChanged ? false : existing?.imageLoadFailed,
    imageRequestStartedAt:
      imageKeyChanged && incomingImageKey ? Date.now() : existing?.imageRequestStartedAt,
    stoppedSince,
  }
}

function zonesEqual(a: ZoneArtworkState, b: ZoneArtworkState): boolean {
  return (
    a.zoneId === b.zoneId &&
    a.displayName === b.displayName &&
    a.zoneAlias === b.zoneAlias &&
    a.groupName === b.groupName &&
    a.state === b.state &&
    a.seekPosition === b.seekPosition &&
    a.queueTimeRemaining === b.queueTimeRemaining &&
    a.shuffle === b.shuffle &&
    a.loop === b.loop &&
    a.autoRadio === b.autoRadio &&
    a.imageKey === b.imageKey &&
    a.artDataUri === b.artDataUri &&
    a.isGrouped === b.isGrouped &&
    a.stoppedSince === b.stoppedSince &&
    (a.groupMembers?.length ?? 0) === (b.groupMembers?.length ?? 0) &&
    (a.groupMembers ?? []).every((m, i) => m === b.groupMembers?.[i]) &&
    isSameNowPlaying(a.nowPlaying, b.nowPlaying) &&
    isSameVolume(a.outputsVolume, b.outputsVolume)
  )
}

function applyZoneSnapshot(
  zones: SnapshotZone[],
  timestamp: number,
  imageCache: ReturnType<typeof useZoneImageCache>,
  setZonesById: React.Dispatch<React.SetStateAction<Record<string, ZoneArtworkState>>>,
  setZoneOrder: React.Dispatch<React.SetStateAction<string[]>>,
  markerTitle: string,
): void {
  setZonesById((prev) => {
    const next: Record<string, ZoneArtworkState> = {}
    let mutated = false
    for (const incoming of zones) {
      const zoneId = incoming.zone_id
      if (!zoneId) continue
      const existing = prev[zoneId]
      const merged = mergeSnapshotZone(incoming, existing, timestamp, imageCache, markerTitle)
      if (existing && zonesEqual(existing, merged)) {
        next[zoneId] = existing
      } else {
        next[zoneId] = merged
        mutated = true
      }
    }
    if (!mutated && Object.keys(prev).length === Object.keys(next).length) {
      return prev
    }
    return next
  })

  const incomingOrder = zones
    .map((z) => z.zone_id)
    .filter((id): id is string => typeof id === 'string')
  setZoneOrder((prev) =>
    prev.length === incomingOrder.length && prev.every((id, i) => id === incomingOrder[i])
      ? prev
      : incomingOrder,
  )
}

interface ZoneStatusPanelProps {
  onZoneCountChange?: (count: number) => void
  defaultZoneName?: string | null
  defaultZoneAlias?: string | null
  defaultZoneGroupName?: string | null
  defaultZoneIsGrouped?: boolean
}

// Read-only playback-setting indicators (shuffle / repeat / auto-radio).
// Each occupies a fixed slot so icons never shift position as they
// toggle — a moving icon is easy to misread. Show-if-on: the slot is
// always present, the glyph inside renders only when the setting is on.
const ZoneSettingsIndicators: React.FC<{ zone: ZoneArtworkState }> = ({ zone }) => {
  const repeatOn = zone.loop !== 'disabled'
  const repeatOne = zone.loop === 'loop_one'
  return (
    <div className={s.zoneSettingsIndicators} aria-label="Playback settings">
      <span className={s.zoneSettingsSlot}>
        {zone.shuffle && (
          <span className={s.zoneSettingsIcon} role="img" aria-label="Shuffle on" title="Shuffle on">
            <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
              <path d="M10.59 9.17 5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z" />
            </svg>
          </span>
        )}
      </span>
      <span className={s.zoneSettingsSlot}>
        {repeatOn && (
          <span
            className={s.zoneSettingsIcon}
            role="img"
            aria-label={repeatOne ? 'Repeat one' : 'Repeat all'}
            title={repeatOne ? 'Repeat one' : 'Repeat all'}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
              <path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z" />
              {repeatOne && <text x="12" y="15" fontSize="9" fontWeight="700" textAnchor="middle">1</text>}
            </svg>
          </span>
        )}
      </span>
      <span className={s.zoneSettingsSlot}>
        {zone.autoRadio && (
          <span className={s.zoneSettingsIcon} role="img" aria-label="Roon Radio on" title="Roon Radio on">
            <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
              <path d="M6.18 15.64a2.43 2.43 0 1 1 0 4.86 2.43 2.43 0 0 1 0-4.86zM4 4.44C12.55 4.44 19.56 11.45 19.56 20h-2.83C16.73 13.01 10.99 7.27 4 7.27V4.44zm0 5.66c5.43 0 9.9 4.47 9.9 9.9h-2.83c0-3.87-3.2-7.07-7.07-7.07V10.1z" />
            </svg>
          </span>
        )}
      </span>
    </div>
  )
}

export const ZoneStatusPanel: React.FC<ZoneStatusPanelProps> = ({ onZoneCountChange, defaultZoneName, defaultZoneAlias, defaultZoneGroupName, defaultZoneIsGrouped }) => {
  const { status, messages, sendMessage, latestZoneSnapshot, trimmedCount } = useWebSocket()
  const commands = useRoonCommands(sendMessage)
  const [zonesById, setZonesById] = React.useState<Record<string, ZoneArtworkState>>({})
  const [zoneOrder, setZoneOrder] = React.useState<string[]>([])

  const [draggingPositions, setDraggingPositions] = React.useState<Record<string, number>>({})
  const [draggingVolumes, setDraggingVolumes] = React.useState<Record<string, number>>({})
  const [volumeModalZoneId, setVolumeModalZoneId] = React.useState<string | null>(null)
  const [expandedVolumeZoneIds, setExpandedVolumeZoneIds] = React.useState<Set<string>>(new Set())
  const [queuesByZoneId, setQueuesByZoneId] = React.useState<Record<string, QueueItem[]>>({})
  const [queueModalZoneId, setQueueModalZoneId] = React.useState<string | null>(null)
  const [clockTick, setClockTick] = React.useState<number>(() => Date.now())
  const [expandedArtworkZoneId, setExpandedArtworkZoneId] = React.useState<string | null>(null)
  const [isExpandedArtworkVisible, setIsExpandedArtworkVisible] = React.useState(false)
  const [pendingExpandRequest, setPendingExpandRequest] = React.useState<PendingExpandRequest | null>(null)
  const [expandedArtworkDataUri, setExpandedArtworkDataUri] = React.useState<string | null>(null)
  // Feature flags from the backend; default optimistic so the button
  // doesn't flicker grey-then-active in the (common) case where the
  // marker is installed.
  const [stopMarkerAvailable, setStopMarkerAvailable] = React.useState<boolean>(true)
  // Hard opt-out: when the agent has DISABLE_SIMULATED_STOP=true, the
  // stop button disappears entirely and stop falls back to pause on
  // both LLM tool and WS button paths. Default false so the button is
  // visible until the broadcast says otherwise.
  const [simulatedStopDisabled, setSimulatedStopDisabled] = React.useState<boolean>(false)
  // Title of the configured silent stop-marker track — feeds the
  // marker filter in mergeSnapshotZone.
  const [stopMarkerTitle, setStopMarkerTitle] = React.useState<string>('')
  // Desktop bundle: the STOP-button setup tooltip points at the Getting
  // Started guide (which has the one-click folder-opening setup).
  const [isBundle, setIsBundle] = React.useState<boolean>(false)
  // Transient error from the most recent failed roon-control-response.
  // Auto-clears after a few seconds via the effect below.
  const [controlError, setControlError] = React.useState<
    { message: string; ts: number } | null
  >(null)
  // True between a verify-button click and the next feature-availability
  // broadcast (or a 3 s safety timeout). Drives a "checking" affordance
  // on the muted stop button so the click doesn't feel ignored
  // when the marker is still missing — without this the button is
  // visually identical before and after the click in the no-change case.
  const [isVerifyingStopMarker, setIsVerifyingStopMarker] = React.useState<boolean>(false)
  const verifyTimeoutRef = React.useRef<number | null>(null)
  const processedIndexRef = React.useRef<number>(0)
  const lastAppliedSnapshotRef = React.useRef<unknown>(null)
  const lastAppliedMarkerTitleRef = React.useRef<string | null>(null)
  const closeExpandedArtworkTimerRef = React.useRef<number | null>(null)

  const imageCache = useZoneImageCache({
    messages,
    trimmedCount,
    sendMessage,
    resetToken: status,
    onImageFailure: React.useCallback((failedKey: string) => {
      setZonesById((prev) => {
        let changed = false
        const next: Record<string, ZoneArtworkState> = { ...prev }
        for (const [zoneId, zone] of Object.entries(prev)) {
          if (zone.imageKey === failedKey && !zone.imageLoadFailed) {
            next[zoneId] = { ...zone, imageLoadFailed: true }
            changed = true
          }
        }
        return changed ? next : prev
      })
    }, []),
    onDefaultArtReady: React.useCallback((imageKey: string, dataUri: string) => {
      setZonesById((prev) => {
        const next: Record<string, ZoneArtworkState> = { ...prev }
        for (const [zoneId, zone] of Object.entries(prev)) {
          if (zone.imageKey === imageKey) {
            next[zoneId] = { ...zone, artDataUri: dataUri, imageLoadFailed: false }
          }
        }
        return next
      })
    }, []),
    resolveExpandRequest: React.useCallback((cacheKey: string, dataUri: string) => {
      setPendingExpandRequest((current) => {
        if (!current || current.cacheKey !== cacheKey) return current
        setExpandedArtworkDataUri(dataUri)
        return null
      })
    }, []),
  })

  // Wall-clock tick driving time-based UI (elapsed since now-playing,
  // stopped-card grace expiry).
  React.useEffect(() => {
    const interval = window.setInterval(() => setClockTick(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [])

  // Auto-clear roon-control error toast a few seconds after it appears.
  React.useEffect(() => {
    if (!controlError) return
    const timer = window.setTimeout(() => setControlError(null), 6000)
    return () => window.clearTimeout(timer)
  }, [controlError])

  // Drop all zone state when the WebSocket disconnects. On reconnect
  // the agent re-emits a fresh snapshot, so cards rebuild from clean
  // state — no risk of stale zones persisting after the outage.
  React.useEffect(() => {
    if (status === 'open') return
    // Syncing local state to the external WebSocket connection
    // state — clearing zone state on disconnect is exactly the kind
    // of "subscribe + sync" pattern the rule's carve-out is for.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setZonesById({})
    setZoneOrder([])
    setQueuesByZoneId({})
    lastAppliedSnapshotRef.current = null
    lastAppliedMarkerTitleRef.current = null
    setVolumeModalZoneId(null)
    setQueueModalZoneId(null)
    setExpandedArtworkZoneId(null)
    setIsExpandedArtworkVisible(false)
    setPendingExpandRequest(null)
    setExpandedArtworkDataUri(null)
    // The verify-broadcast we were waiting on can't arrive across a
    // disconnect; clear the in-flight indicator + safety timeout so
    // the button isn't stuck in "Checking…" after reconnect.
    if (verifyTimeoutRef.current !== null) {
      window.clearTimeout(verifyTimeoutRef.current)
      verifyTimeoutRef.current = null
    }
    setIsVerifyingStopMarker(false)
  }, [status])

  // Clear the in-flight verify timeout on unmount so we don't try to
  // set state on an unmounted component if the panel goes away mid-check.
  React.useEffect(() => () => {
    if (verifyTimeoutRef.current !== null) {
      window.clearTimeout(verifyTimeoutRef.current)
      verifyTimeoutRef.current = null
    }
  }, [])

  React.useEffect(() => {
    // W1: Adjust for messages trimmed from the front of the array.
    const relativeIdx = Math.max(0, processedIndexRef.current - trimmedCount)
    const nextMessages = messages.slice(relativeIdx)
    processedIndexRef.current = messages.length + trimmedCount

    // Pre-scan for the marker title so a same-batch feature-
    // availability is honoured before the snapshot is applied below
    // (setStopMarkerTitle wouldn't propagate until the next render).
    let effectiveMarkerTitle = stopMarkerTitle
    for (const message of nextMessages) {
      if (message.direction !== 'inbound' || message.channel !== 'feature-availability') continue
      const payload = parseJson<{ stop_marker_title?: string }>(message.payload ?? message.body)
      if (payload && typeof payload.stop_marker_title === 'string') {
        effectiveMarkerTitle = payload.stop_marker_title
      }
    }

    for (const message of nextMessages) {
      if (message.direction !== 'inbound') continue

      if (message.channel === 'roon-control-response') {
        const payload = parseJson<RoonControlResponsePayload>(message.payload ?? message.body)
        if (!payload) continue
        if (!payload.ok && payload.error) {
          console.warn('Roon control error:', payload.error)
          setControlError({ message: payload.error, ts: Date.now() })
        }
      }

      if (message.channel === 'feature-availability') {
        const payload = parseJson<{
          stop_marker_available?: boolean
          simulated_stop_disabled?: boolean
          stop_marker_title?: string
          is_bundle?: boolean
        }>(message.payload ?? message.body)
        if (payload && typeof payload.stop_marker_available === 'boolean') {
          setStopMarkerAvailable(payload.stop_marker_available)
        }
        if (payload && typeof payload.simulated_stop_disabled === 'boolean') {
          setSimulatedStopDisabled(payload.simulated_stop_disabled)
        }
        if (payload && typeof payload.stop_marker_title === 'string') {
          setStopMarkerTitle(payload.stop_marker_title)
        }
        if (payload && typeof payload.is_bundle === 'boolean') {
          setIsBundle(payload.is_bundle)
        }
        // Any feature-availability broadcast clears the in-flight
        // verify indicator (agent emits one even on no-change, so the
        // timeout is just a safety net).
        if (verifyTimeoutRef.current !== null) {
          window.clearTimeout(verifyTimeoutRef.current)
          verifyTimeoutRef.current = null
        }
        setIsVerifyingStopMarker(false)
      }

      if (message.channel === 'queue-updates') {
        const payload = parseJson<QueueUpdatePayload>(message.payload ?? message.body)
        if (!payload?.zone_id || !payload.items) continue
        setQueuesByZoneId((prev) => ({ ...prev, [payload.zone_id]: payload.items }))
      }
    }

    // Skip redundant applies — the ref-dedup gates this on the
    // snapshot or effective marker title actually changing.
    if (
      latestZoneSnapshot &&
      (
        latestZoneSnapshot !== lastAppliedSnapshotRef.current ||
        effectiveMarkerTitle !== lastAppliedMarkerTitleRef.current
      )
    ) {
      const event = latestZoneSnapshot as ZoneSnapshotEvent
      const incomingZones = event?.data?.zones ?? []
      const timestamp = (event?.data?.timestamp_ms ?? Date.now()) / 1000
      applyZoneSnapshot(incomingZones, timestamp, imageCache, setZonesById, setZoneOrder, effectiveMarkerTitle)
      lastAppliedSnapshotRef.current = latestZoneSnapshot
      lastAppliedMarkerTitleRef.current = effectiveMarkerTitle
    }
  }, [messages, latestZoneSnapshot, trimmedCount, imageCache, stopMarkerTitle])
  const zoneEntries = React.useMemo(() => {
    const nowSec = clockTick / 1000
    return zoneOrder
      .map((id) => zonesById[id])
      .filter((z): z is ZoneArtworkState => z != null)
      .filter((z) => {
        if (z.state !== 'stopped') return true
        if (z.stoppedSince === undefined) return true
        return nowSec - z.stoppedSince < FINISHED_ZONE_IDLE_SECONDS
      })
  }, [zonesById, zoneOrder, clockTick])
  React.useEffect(() => {
    onZoneCountChange?.(zoneEntries.length)
  }, [zoneEntries.length, onZoneCountChange])

  const expandedArtworkZone = React.useMemo(
    () => (expandedArtworkZoneId ? zonesById[expandedArtworkZoneId] ?? null : null),
    [expandedArtworkZoneId, zonesById],
  )
  const getFullscreenArtworkDimensions = React.useCallback(() => {
    const maxSide = Math.min(
      MAX_FULLSCREEN_ART_DIMENSION,
      Math.max(DEFAULT_ART_WIDTH, Math.floor(window.innerWidth * Math.max(1, window.devicePixelRatio || 1))),
    )
    return { width: maxSide, height: maxSide }
  }, [])

  const closeExpandedArtwork = React.useCallback(() => {
    setPendingExpandRequest(null)
    setIsExpandedArtworkVisible(false)
    if (closeExpandedArtworkTimerRef.current !== null) {
      window.clearTimeout(closeExpandedArtworkTimerRef.current)
    }
    closeExpandedArtworkTimerRef.current = window.setTimeout(() => {
      setExpandedArtworkZoneId(null)
      setExpandedArtworkDataUri(null)
      closeExpandedArtworkTimerRef.current = null
    }, ARTWORK_OVERLAY_ANIMATION_MS)
  }, [])

  const openExpandedArtwork = React.useCallback(
    (zoneId: string, imageKey: string) => {
      const { width, height } = getFullscreenArtworkDimensions()
      const fullscreenCacheKey = imageCacheKey(imageKey, width, height)
      if (closeExpandedArtworkTimerRef.current !== null) {
        window.clearTimeout(closeExpandedArtworkTimerRef.current)
        closeExpandedArtworkTimerRef.current = null
      }
      const cached = imageCache.lookup(imageKey, width, height)
      if (cached !== undefined) {
        setPendingExpandRequest(null)
        setExpandedArtworkZoneId(zoneId)
        setExpandedArtworkDataUri(cached)
        setIsExpandedArtworkVisible(true)
        return
      }
      setExpandedArtworkZoneId(zoneId)
      setExpandedArtworkDataUri(null)
      setIsExpandedArtworkVisible(true)
      setPendingExpandRequest({ zoneId, cacheKey: fullscreenCacheKey })
      imageCache.requestIfMissing(imageKey, width, height)
    },
    [getFullscreenArtworkDimensions, imageCache],
  )

  React.useEffect(
    () => () => {
      if (closeExpandedArtworkTimerRef.current !== null) {
        window.clearTimeout(closeExpandedArtworkTimerRef.current)
      }
    },
    [],
  )

  React.useEffect(() => {
    if (expandedVolumeZoneIds.size === 0) return
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      const volumeRow = target.closest('[data-volume-zone-id]')
      if (volumeRow && expandedVolumeZoneIds.has(volumeRow.getAttribute('data-volume-zone-id') ?? '')) {
        return
      }
      setExpandedVolumeZoneIds(new Set())
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [expandedVolumeZoneIds])

  React.useEffect(() => {
    for (const zone of zoneEntries) {
      if (!zone.imageKey || zone.artDataUri) {
        continue
      }
      imageCache.requestIfMissing(zone.imageKey, DEFAULT_ART_WIDTH, DEFAULT_ART_HEIGHT)
    }
  }, [imageCache, zoneEntries])

  const getDisplayedPosition = (zone: ZoneArtworkState): number => {
    const length = Number(zone.nowPlaying.length ?? 0)
    if (length <= 0) return 0
    const dragging = draggingPositions[zone.zoneId]
    if (typeof dragging === 'number') {
      return Math.min(length, Math.max(0, dragging))
    }
    const base = Math.max(0, Number(zone.seekPosition ?? 0))
    if (zone.state !== 'playing') {
      return Math.min(length, base)
    }
    const elapsed = Math.max(0, Math.floor(clockTick / 1000 - zone.lastUpdatedAt))
    return Math.min(length, base + elapsed)
  }

  const commitSeek = (zone: ZoneArtworkState, position: number) => {
    const length = Math.max(0, Number(zone.nowPlaying.length ?? 0))
    const clampedPosition = Math.min(length > 0 ? length : Number.MAX_SAFE_INTEGER, Math.max(0, position))
    setDraggingPositions((prev) => {
      const next = { ...prev }
      delete next[zone.zoneId]
      return next
    })
    setZonesById((prev) => {
      const existing = prev[zone.zoneId]
      if (!existing) return prev
      return {
        ...prev,
        [zone.zoneId]: {
          ...existing,
          seekPosition: clampedPosition,
          lastUpdatedAt: Math.floor(Date.now() / 1000),
        },
      }
    })
    commands.zoneCommand(zone.displayName, 'seek', clampedPosition)
  }

  const commitVolume = (outputName: string, value: number) => {
    setDraggingVolumes((prev) => {
      const next = { ...prev }
      delete next[outputName]
      return next
    })
    commands.setVolume(outputName, value)
  }

  return (
    <div className={`panel ${s.panelZoneStatus}`}>
      <div className="panel-header">
        <span className="panel-heading-group">
          <h3>Now Playing</h3>
          <GuidanceButton id="zones" />
        </span>
        <span className={s.zoneCount}>{zoneEntries.length} zone{zoneEntries.length === 1 ? '' : 's'}</span>
      </div>

      {controlError && (
        <div
          role="alert"
          style={{
            margin: '8px 12px',
            padding: '8px 10px',
            background: 'var(--color-error-bg, rgba(220, 50, 50, 0.12))',
            border: '1px solid var(--color-error-border, rgba(220, 50, 50, 0.4))',
            borderRadius: '4px',
            fontSize: '0.875rem',
            color: 'var(--color-error-text, inherit)',
          }}
        >
          {controlError.message}
        </div>
      )}

      <div className={`panel-body scrollable ${s.zoneListScroll}`}>
        {status !== 'open' || zoneEntries.length === 0 ? (
          <div className={s.zoneEmptyState}>
            <div className={s.zoneEmptyArtwork}>
              <span className={`${s.zoneArtworkPlaceholder} ${status !== 'open' ? s.zoneArtworkPlaceholderError : ''}`}>
                {status === 'open' ? 'Nothing playing' : 'Disconnected'}
              </span>
            </div>
            {defaultZoneName && <p className={`${s.zoneEmptyZoneName} ${defaultZoneIsGrouped ? s.zoneEmptyZoneNameGrouped : ''}`}>{formatZoneLabel(defaultZoneName, defaultZoneAlias, defaultZoneGroupName)}</p>}
            <p className={s.zoneEmptyHint}>
              {status === 'open'
                ? 'Zones will appear here when music starts playing.'
                : 'Zones will reappear once the connection is restored.'}
            </p>
          </div>
        ) : (
          <ul className={`${s.zoneCardList}${!simulatedStopDisabled ? ` ${s.zoneCardListWithStop}` : ''}`}>
            {zoneEntries.map((zone) => (
              <li key={zone.zoneId} className={s.zoneCard}>
                <div className={s.zoneCardTop}>
                  <strong className={`${s.zoneName} ${zone.isGrouped ? s.zoneNameGrouped : ''}`}>
                    {formatZoneLabel(zone.displayName, zone.zoneAlias, zone.groupName)}
                  </strong>
                  <span className={`${s.zoneState} ${ZONE_STATE_CLASS[zone.state] ?? s.zoneStateStopped}`}>
                    {(zone.state || 'unknown').toUpperCase()}
                  </span>
                </div>
                {zone.isGrouped && zone.groupMembers && zone.groupMembers.length > 1 && (
                  <div className={s.zoneGroupInfo}>
                    {zone.groupMembers.join(' + ')}
                  </div>
                )}
                <div className={s.zoneCardContent}>
                  <div
                    className={`${s.zoneArtworkFrame} ${zone.state !== 'stopped' && zone.artDataUri ? s.zoneArtworkFrameClickable : ''}`}
                    onClick={() => {
                      if (zone.state === 'stopped' || !zone.artDataUri) return
                      if (expandedArtworkZoneId === zone.zoneId && isExpandedArtworkVisible) {
                        if (pendingExpandRequest?.zoneId === zone.zoneId) return
                        closeExpandedArtwork()
                        return
                      }
                      if (!zone.imageKey) return
                      openExpandedArtwork(zone.zoneId, zone.imageKey)
                    }}
                    role={zone.state !== 'stopped' && zone.artDataUri ? 'button' : undefined}
                    tabIndex={zone.state !== 'stopped' && zone.artDataUri ? 0 : undefined}
                    onKeyDown={(event) => {
                      if (zone.state === 'stopped' || !zone.artDataUri) return
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        if (expandedArtworkZoneId === zone.zoneId && isExpandedArtworkVisible) {
                          if (pendingExpandRequest?.zoneId === zone.zoneId) return
                          closeExpandedArtwork()
                          return
                        }
                        if (!zone.imageKey) return
                        openExpandedArtwork(zone.zoneId, zone.imageKey)
                      }
                    }}
                    aria-label={
                      zone.state !== 'stopped' && zone.artDataUri
                        ? `Expand artwork for ${zone.nowPlaying.line1 || zone.displayName}`
                        : undefined
                    }
                  >
                    {zone.state !== 'stopped' && zone.artDataUri ? (
                      <img
                        src={zone.artDataUri}
                        alt={`Artwork for ${zone.nowPlaying.line1 || zone.displayName}`}
                        className={s.zoneArtworkImage}
                      />
                    ) : (
                      <div className={s.zoneArtworkPlaceholder}>
                        {artworkPlaceholderText(zone, clockTick)}
                      </div>
                    )}
                  </div>
                  <div className={s.zoneDetails}>
                    <div className={s.zoneTrackLines}>
                      <strong>
                        <span
                          className={`${s.playingIndicator} ${zone.state === 'playing' ? s.playingIndicatorActive : ''}`}
                          aria-hidden="true"
                          data-testid={zone.state === 'playing' ? 'zone-playing-indicator' : undefined}
                        >
                          <i />
                          <i />
                          <i />
                        </span>
                        {zone.nowPlaying.line1 || 'Nothing playing'}
                      </strong>
                      <span>{zone.nowPlaying.line2 || '\u2014'}</span>
                      <span>{zone.nowPlaying.line3 || '\u2014'}</span>
                    </div>
                    <div className={s.zoneCardActions}>
                      <button
                        type="button"
                        className={`${s.zoneActionButton}${zone.state === 'playing' ? ` ${s.zoneActionButtonPlaying}` : ''}`}
                        disabled={status !== 'open' || zone.state === 'stopped'}
                        title={status === 'open' ? 'Play' : 'Controls unavailable: websocket is not connected'}
                        aria-label="Play"
                        onClick={() => commands.zoneCommand(zone.displayName, 'play')}
                      >
                        <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><polygon points="6,4 20,12 6,20" /></svg>
                      </button>
                      <button
                        type="button"
                        className={`${s.zoneActionButton}${zone.state === 'paused' ? ` ${s.zoneActionButtonPaused}` : ''}`}
                        disabled={status !== 'open' || zone.state === 'stopped'}
                        title={status === 'open' ? 'Pause' : 'Controls unavailable: websocket is not connected'}
                        aria-label="Pause"
                        onClick={() => commands.zoneCommand(zone.displayName, 'pause')}
                      >
                        <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><rect x="5" y="4" width="4" height="16" /><rect x="15" y="4" width="4" height="16" /></svg>
                      </button>
                      {!simulatedStopDisabled && (
                        <button
                          type="button"
                          className={
                            stopMarkerAvailable
                              ? `${s.zoneActionButton}${zone.state === 'stopped' ? ` ${s.zoneActionButtonStopped}` : ''}`
                              : `${s.zoneActionButton} ${s.zoneActionButtonWaiting}${isVerifyingStopMarker ? ` ${s.zoneActionButtonVerifying}` : ''}`
                          }
                          disabled={status !== 'open' || (!stopMarkerAvailable && isVerifyingStopMarker) || (stopMarkerAvailable && zone.state === 'stopped')}
                          title={stopButtonTooltip({
                            connected: status === 'open',
                            stopMarkerAvailable,
                            isVerifying: isVerifyingStopMarker,
                            isBundle,
                          })}
                          aria-label={
                            stopMarkerAvailable
                              ? 'Stop'
                              : (isVerifyingStopMarker
                                  ? 'Checking for stop marker'
                                  : 'Verify stop marker availability')
                          }
                          onClick={() => {
                            if (stopMarkerAvailable) {
                              commands.zoneCommand(zone.displayName, 'stop')
                              return
                            }
                            // Verify path: kick off the in-flight indicator
                            // *before* sending so the user sees feedback the
                            // moment they click. The state clears when the
                            // agent's broadcast lands, or after 3 s as a
                            // safety net for a dropped reply.
                            setIsVerifyingStopMarker(true)
                            if (verifyTimeoutRef.current !== null) {
                              window.clearTimeout(verifyTimeoutRef.current)
                            }
                            verifyTimeoutRef.current = window.setTimeout(() => {
                              setIsVerifyingStopMarker(false)
                              verifyTimeoutRef.current = null
                            }, 3000)
                            commands.verifyFeature('stop_marker')
                          }}
                        >
                          <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><rect x="5" y="5" width="14" height="14" /></svg>
                        </button>
                      )}
                      <button
                        type="button"
                        className={s.zoneActionButton}
                        disabled={status !== 'open' || zone.state === 'stopped'}
                        title={status === 'open' ? 'Previous' : 'Controls unavailable: websocket is not connected'}
                        aria-label="Previous track"
                        onClick={() => commands.zoneCommand(zone.displayName, 'previous')}
                      >
                        <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><rect x="2" y="4" width="3" height="16" /><polygon points="22,4 9,12 22,20" /></svg>
                      </button>
                      <button
                        type="button"
                        className={s.zoneActionButton}
                        disabled={status !== 'open' || zone.state === 'stopped'}
                        title={status === 'open' ? 'Next' : 'Controls unavailable: websocket is not connected'}
                        aria-label="Next track"
                        onClick={() => commands.zoneCommand(zone.displayName, 'next')}
                      >
                        <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><polygon points="2,4 15,12 2,20" /><rect x="19" y="4" width="3" height="16" /></svg>
                      </button>
                      <button
                        type="button"
                        className={`${s.zoneActionButton} ${s.zoneQueueButton}`}
                        disabled={status !== 'open' || !(queuesByZoneId[zone.zoneId]?.length)}
                        title="Queue"
                        aria-label="Queue"
                        onClick={() => setQueueModalZoneId(zone.zoneId)}
                      >
                        &#9776;
                        {(queuesByZoneId[zone.zoneId]?.length ?? 0) > 0 && (
                          <span className={s.zoneQueueBadge}>{queuesByZoneId[zone.zoneId].length}</span>
                        )}
                      </button>
                    </div>
                  </div>
                </div>
                <div className={s.zoneProgressRow}>
                  <div className={s.zoneProgressTimes}>
                    <span className={s.zoneProgressTime}>{formatDuration(getDisplayedPosition(zone))}</span>
                    <span className={s.zoneProgressTime}>{formatDuration(Number(zone.nowPlaying.length ?? 0))}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={Math.max(1, Number(zone.nowPlaying.length ?? 0))}
                    value={getDisplayedPosition(zone)}
                    className={s.zoneProgressSlider}
                    disabled={status !== 'open' || Number(zone.nowPlaying.length ?? 0) <= 0}
                    aria-label="Track position"
                    aria-valuetext={`${formatDuration(getDisplayedPosition(zone))} of ${formatDuration(Number(zone.nowPlaying.length ?? 0))}`}
                    onChange={(event) => {
                      const value = Number(event.target.value)
                      setDraggingPositions((prev) => ({ ...prev, [zone.zoneId]: value }))
                    }}
                    onMouseUp={(event) => commitSeek(zone, Number((event.target as HTMLInputElement).value))}
                    onTouchEnd={(event) => commitSeek(zone, Number((event.target as HTMLInputElement).value))}
                  />
                </div>
                <div className={s.zoneControlsLine}>
                {zone.outputsVolume.length === 1 && (() => {
                  const vol = zone.outputsVolume[0]
                  if (!vol.type) {
                    return (
                      <div className={s.zoneVolumeRow}>
                        <span className={s.zoneVolumeIcon}>&#128264;</span>
                        <span className={s.zoneVolumeFixed}>Fixed volume</span>
                      </div>
                    )
                  }
                  const dragKey = vol.name
                  const displayValue = draggingVolumes[dragKey] ?? vol.value ?? 0
                  const isVolumeExpanded = expandedVolumeZoneIds.has(zone.zoneId)
                  const toggleVolume = () => setExpandedVolumeZoneIds((prev) => {
                    const next = new Set(prev)
                    if (next.has(zone.zoneId)) next.delete(zone.zoneId)
                    else next.add(zone.zoneId)
                    return next
                  })
                  return (
                    <div className={s.zoneVolumeRow} data-volume-zone-id={zone.zoneId}>
                      <button
                        type="button"
                        className={s.zoneVolumeMute}
                        onClick={() => commands.mute(vol.name, !vol.is_muted)}
                        disabled={status !== 'open'}
                        title={vol.is_muted ? 'Unmute' : 'Mute'}
                        aria-label={vol.is_muted ? `Unmute ${vol.name}` : `Mute ${vol.name}`}
                      >
                        {vol.is_muted ? '\u{1F507}' : '\u{1F50A}'}
                      </button>
                      {isVolumeExpanded ? (
                        <>
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
                            onChange={(e) => setDraggingVolumes((prev) => ({ ...prev, [dragKey]: Number(e.target.value) }))}
                            onMouseUp={(e) => commitVolume(vol.name, Number((e.target as HTMLInputElement).value))}
                            onTouchEnd={(e) => commitVolume(vol.name, Number((e.target as HTMLInputElement).value))}
                          />
                          <button type="button" className={s.zoneVolumeToggle} onClick={toggleVolume} aria-label="Hide volume slider">
                            {formatVolumePercent(displayValue, vol.min, vol.max)}
                          </button>
                        </>
                      ) : (
                        <button type="button" className={s.zoneVolumeToggle} onClick={toggleVolume} aria-label="Show volume slider">
                          {formatVolumePercent(displayValue, vol.min, vol.max)}
                        </button>
                      )}
                    </div>
                  )
                })()}
                {zone.outputsVolume.length > 1 && (() => {
                  const hasVariable = zone.outputsVolume.some((v) => v.type != null)
                  if (!hasVariable) {
                    return (
                      <div className={s.zoneVolumeRow}>
                        <span className={s.zoneVolumeIcon}>&#128264;</span>
                        <span className={s.zoneVolumeFixed}>Fixed volume</span>
                      </div>
                    )
                  }
                  return (
                    <div className={s.zoneVolumeRow}>
                      <button
                        type="button"
                        className={s.zoneVolumeGroupButton}
                        onClick={() => setVolumeModalZoneId(zone.zoneId)}
                        title="Volume controls for each output in this group"
                      >
                        <span className={s.zoneVolumeIcon}>&#128266;</span>
                        <span>Volume ({zone.outputsVolume.length} outputs)</span>
                      </button>
                    </div>
                  )
                })()}
                <ZoneSettingsIndicators zone={zone} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {expandedArtworkZone &&
      expandedArtworkZone.state !== 'stopped' &&
      (expandedArtworkDataUri || pendingExpandRequest?.zoneId === expandedArtworkZone.zoneId) ? (
        <ArtworkOverlay
          zone={expandedArtworkZone}
          dataUri={expandedArtworkDataUri}
          visible={isExpandedArtworkVisible}
          onClose={closeExpandedArtwork}
        />
      ) : null}

      {volumeModalZoneId && (() => {
        const modalZone = zonesById[volumeModalZoneId]
        if (!modalZone || modalZone.outputsVolume.length <= 1) return null
        return (
          <VolumeModal
            zone={modalZone}
            draggingVolumes={draggingVolumes}
            status={status}
            onDragChange={(outputName, value) => setDraggingVolumes((prev) => ({ ...prev, [outputName]: value }))}
            onCommit={commitVolume}
            onMute={commands.mute}
            onClose={() => setVolumeModalZoneId(null)}
          />
        )
      })()}

      {queueModalZoneId && (() => {
        const modalZone = zonesById[queueModalZoneId]
        const queueItems = queuesByZoneId[queueModalZoneId]
        if (!modalZone || !queueItems?.length) return null
        return (
          <QueueModal
            zone={modalZone}
            items={queueItems}
            timeRemaining={modalZone.queueTimeRemaining}
            status={status}
            onPlayFromHere={commands.playFromHere}
            onClose={() => setQueueModalZoneId(null)}
          />
        )
      })()}
    </div>
  )
}
