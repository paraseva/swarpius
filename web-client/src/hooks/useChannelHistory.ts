import React from 'react'
import { type SocketMessage, useWebSocket } from '../websocketContext'
import { useStickyBottomScroll } from './useStickyBottomScroll'
import { useHistoryScrollback } from './useHistoryScrollback'
import { useRequestFocusSync } from './useRequestFocusSync'

/**
 * The single per-channel history behaviour every panel inherits: seed this
 * channel's recent day, follow live at the bottom, scroll up to load the
 * previous day-with-data (holding scroll position so it doesn't jerk), and on a
 * request-id badge focus elsewhere, load that request's day for this channel and
 * jump to it. No global state — everything is keyed by `channel`, so panels load
 * independently and never disturb each other.
 *
 * Returns this channel's slice of the shared message stream (ready to render).
 */
export function useChannelHistory<T extends HTMLElement>(
  channel: string,
  scrollRef: React.RefObject<T | null>,
  syncKey?: string,
): SocketMessage[] {
  const {
    messages, requestHistory, reachedBeginningByChannel, historyBatchTokenByChannel,
  } = useWebSocket()

  const channelMessages = React.useMemo(
    () => messages.filter((m) => m.channel === channel),
    [messages, channel],
  )

  const loadBefore = React.useCallback(
    (beforeMs: number) => requestHistory?.(beforeMs, channel),
    [requestHistory, channel],
  )

  // Seed this channel's most-recent day on mount/reconnect.
  React.useEffect(() => {
    requestHistory?.(Date.now(), channel)
  }, [requestHistory, channel])

  useStickyBottomScroll(scrollRef, `history:${channel}`)
  useHistoryScrollback(
    scrollRef, channelMessages, loadBefore,
    reachedBeginningByChannel?.[channel] ?? false,
    historyBatchTokenByChannel?.[channel] ?? 0,
  )
  useRequestFocusSync(scrollRef, syncKey, channel)

  return channelMessages
}
