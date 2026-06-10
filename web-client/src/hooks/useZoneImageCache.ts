import React from 'react'
import { createUuid } from '../utils/uuid'
import {
  type ImageResponsePayload,
  DEFAULT_ART_HEIGHT,
  DEFAULT_ART_WIDTH,
} from '../components/zoneStatusModel'
import { imageCacheKey, parseJson } from '../components/zoneStatusUtils'
import type { SocketMessage } from '../websocketContext'

const MAX_IMAGE_CACHE_SIZE = 50

/** Insert into a Map-based LRU cache, evicting the oldest entries when over capacity. */
const cacheImage = (cache: Map<string, string>, key: string, value: string) => {
  cache.delete(key)
  cache.set(key, value)
  while (cache.size > MAX_IMAGE_CACHE_SIZE) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
    else break
  }
}

export interface UseZoneImageCacheArgs {
  messages: SocketMessage[]
  trimmedCount: number
  sendMessage: (channel: string, body: string) => string
  /** Mark a zone's artwork slot as failed when the server reports a
   *  specific image_key failed to load. */
  onImageFailure: (imageKey: string) => void
  /** Hand the decoded data URI back for the default card thumbnail —
   *  applied to matching zones by the panel's setZonesById callback. */
  onDefaultArtReady: (imageKey: string, dataUri: string) => void
  /** Hand the decoded data URI back to the fullscreen overlay if the
   *  response matches a pending expand request. Returning true clears
   *  the pending request. */
  resolveExpandRequest: (cacheKey: string, dataUri: string) => void
  /** Reset signal — clear internal state on WebSocket disconnect. */
  resetToken: unknown
}

export interface ZoneImageCache {
  /** Look up a cached data URI by (imageKey, width, height). */
  lookup: (imageKey: string, width: number, height: number) => string | undefined
  /** Request the given image if it isn't cached or already in flight. */
  requestIfMissing: (imageKey: string, width: number, height: number) => void
}

/**
 * Owns the per-panel image cache ref + the in-flight request set, and
 * consumes roon-image-response messages. Splitting the cache
 * bookkeeping out of ZoneStatusPanel keeps its invariants (LRU
 * eviction, dedup-in-flight, propagation of successes/failures to the
 * zone state) in one place.
 */
export function useZoneImageCache({
  messages,
  trimmedCount,
  sendMessage,
  onImageFailure,
  onDefaultArtReady,
  resolveExpandRequest,
  resetToken,
}: UseZoneImageCacheArgs): ZoneImageCache {
  const imageCacheRef = React.useRef<Map<string, string>>(new Map())
  const pendingImageRequestsRef = React.useRef<Set<string>>(new Set())
  const processedIndexRef = React.useRef<number>(0)

  // Stable callbacks via refs so the main scan effect doesn't re-run
  // every time the caller re-creates its handlers.
  const onImageFailureRef = React.useRef(onImageFailure)
  const onDefaultArtReadyRef = React.useRef(onDefaultArtReady)
  const resolveExpandRef = React.useRef(resolveExpandRequest)
  React.useEffect(() => { onImageFailureRef.current = onImageFailure }, [onImageFailure])
  React.useEffect(() => { onDefaultArtReadyRef.current = onDefaultArtReady }, [onDefaultArtReady])
  React.useEffect(() => { resolveExpandRef.current = resolveExpandRequest }, [resolveExpandRequest])

  // Reset on disconnect so a reconnect starts from a clean cache.
  React.useEffect(() => {
    pendingImageRequestsRef.current.clear()
  }, [resetToken])

  React.useEffect(() => {
    const relativeIdx = Math.max(0, processedIndexRef.current - trimmedCount)
    const nextMessages = messages.slice(relativeIdx)
    processedIndexRef.current = messages.length + trimmedCount
    if (nextMessages.length === 0) return

    for (const message of nextMessages) {
      if (message.direction !== 'inbound') continue
      if (message.channel !== 'roon-image-response') continue

      const payload = parseJson<ImageResponsePayload>(message.payload ?? message.body)
      if (!payload) continue

      if (!payload.ok || !payload.image_key || !payload.base64_data || !payload.mime_type) {
        if (payload.error) {
          console.warn('Zone artwork load failed:', payload.error)
        }
        if (payload.image_key) {
          onImageFailureRef.current(payload.image_key)
        }
        continue
      }

      const width = payload.width ?? DEFAULT_ART_WIDTH
      const height = payload.height ?? DEFAULT_ART_HEIGHT
      const key = imageCacheKey(payload.image_key, width, height)
      const dataUri = `data:${payload.mime_type};base64,${payload.base64_data}`
      cacheImage(imageCacheRef.current, key, dataUri)
      pendingImageRequestsRef.current.delete(key)
      resolveExpandRef.current(key, dataUri)

      if (width === DEFAULT_ART_WIDTH && height === DEFAULT_ART_HEIGHT) {
        onDefaultArtReadyRef.current(payload.image_key, dataUri)
      }
    }
  }, [messages, trimmedCount])

  const lookup = React.useCallback(
    (imageKey: string, width: number, height: number) =>
      imageCacheRef.current.get(imageCacheKey(imageKey, width, height)),
    [],
  )

  const requestIfMissing = React.useCallback(
    (imageKey: string, width: number, height: number) => {
      const key = imageCacheKey(imageKey, width, height)
      if (imageCacheRef.current.has(key) || pendingImageRequestsRef.current.has(key)) return
      pendingImageRequestsRef.current.add(key)
      sendMessage(
        'roon-image-request',
        JSON.stringify({
          request_id: createUuid(),
          image_key: imageKey,
          width,
          height,
        }),
      )
    },
    [sendMessage],
  )

  return React.useMemo(() => ({ lookup, requestIfMissing }), [lookup, requestIfMissing])
}
