import React from 'react'
import { parseJson } from '../utils/parseJson'
import type { SocketMessage } from '../websocketContext'

export interface RateLimitState {
  countdown: number
  attempt: number
  maxRetries: number
  canOverride: boolean
  agentName: string
  error: string
}

interface UiBannerBase {
  id: string
  agentName: string
  error: string
}

export type UiBanner =
  | ({ kind: 'retry' } & UiBannerBase & RateLimitState)
  | ({ kind: 'error' } & UiBannerBase)

interface RateLimitPayload {
  active?: boolean
  retriable?: boolean
  retry_in_seconds?: number
  attempt?: number
  max_retries?: number
  can_override?: boolean
  agent_name?: string
  error?: string
  display_seconds?: number
}

interface SessionControlResponse {
  ok?: boolean
  action?: string
  error?: string
}

const MIN_RATE_LIMIT_BANNER_MS = 1500

export interface ChatBannerManager {
  banners: UiBanner[]
  isRateLimited: boolean
  /** Show a transient error banner that auto-dismisses after `displaySeconds`.
   * Exposed so callers (e.g. TTS error handler) can add their own entries. */
  addTransientErrorBanner: (
    agentName: string,
    error: string,
    displaySeconds?: number,
    id?: string,
  ) => void
}

/**
 * Own banner state for the chat panel: rate-limit retry countdowns,
 * coordinator/system errors, and session-control error surfaces. Scans
 * `messages` since the last render and translates matching events into
 * a deduped banner list with per-banner auto-dismiss timers.
 */
export function useChatBannerManager(
  messages: SocketMessage[],
  trimmedCount: number,
): ChatBannerManager {
  const [banners, setBanners] = React.useState<UiBanner[]>([])
  const processedMessageCountRef = React.useRef(0)
  const retryVisibleUntilRef = React.useRef<Map<string, number>>(new Map())
  const bannerTimersRef = React.useRef<Map<string, number>>(new Map())

  const clearBannerTimer = React.useCallback((bannerId: string) => {
    const timer = bannerTimersRef.current.get(bannerId)
    if (typeof timer === 'number') {
      window.clearTimeout(timer)
      bannerTimersRef.current.delete(bannerId)
    }
  }, [])

  const scheduleBannerRemoval = React.useCallback(
    (bannerId: string, delayMs: number) => {
      clearBannerTimer(bannerId)
      const timer = window.setTimeout(() => {
        setBanners((prev) => prev.filter((banner) => banner.id !== bannerId))
        bannerTimersRef.current.delete(bannerId)
      }, Math.max(0, delayMs))
      bannerTimersRef.current.set(bannerId, timer)
    },
    [clearBannerTimer],
  )

  const upsertBanner = React.useCallback((nextBanner: UiBanner) => {
    setBanners((prev) => {
      const existingIdx = prev.findIndex((banner) => banner.id === nextBanner.id)
      if (existingIdx === -1) {
        return [nextBanner, ...prev]
      }
      const next = [...prev]
      next[existingIdx] = nextBanner
      return next
    })
  }, [])

  const addTransientErrorBanner = React.useCallback(
    (agentName: string, error: string, displaySeconds = 5, id?: string) => {
      const bannerId = id ?? `err-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      upsertBanner({
        id: bannerId,
        kind: 'error',
        agentName,
        error,
      })
      scheduleBannerRemoval(bannerId, Math.max(1, displaySeconds) * 1000)
    },
    [scheduleBannerRemoval, upsertBanner],
  )

  React.useEffect(
    () => () => {
      for (const timerId of bannerTimersRef.current.values()) {
        window.clearTimeout(timerId)
      }
      bannerTimersRef.current.clear()
    },
    [],
  )

  React.useEffect(() => {
    const absoluteIdx = processedMessageCountRef.current
    const startIdx = Math.max(0, absoluteIdx - trimmedCount)
    if (startIdx >= messages.length) return

    for (let idx = startIdx; idx < messages.length; idx += 1) {
      const msg = messages[idx]
      if (msg.direction !== 'inbound') continue
      // Replayed events describe past state; surfacing them as banners
      // would resurrect dismissed errors and stale rate-limit countdowns
      // on every refresh.
      if (msg.meta?.replay === true) continue

      if (msg.channel === 'rate-limit') {
        const parsed = parseJson<RateLimitPayload>(msg.payload ?? msg.body)
        if (!parsed) continue
        const agentName = (parsed.agent_name || 'LLM Agent').trim()
        const errorText = (parsed.error || 'Unknown LLM error').trim()
        const retryBannerId = `llm-retry-${agentName.toLowerCase()}`
        if (parsed.retriable === false) {
          addTransientErrorBanner(
            agentName,
            errorText,
            Math.max(1, parsed.display_seconds ?? 5),
            `llm-error-${agentName.toLowerCase()}-${msg.id}`,
          )
          continue
        }
        if (parsed.active) {
          retryVisibleUntilRef.current.set(retryBannerId, Date.now() + MIN_RATE_LIMIT_BANNER_MS)
          upsertBanner({
            id: retryBannerId,
            kind: 'retry',
            countdown: parsed.retry_in_seconds ?? 0,
            attempt: parsed.attempt ?? 0,
            maxRetries: parsed.max_retries ?? 0,
            canOverride: parsed.can_override ?? false,
            agentName,
            error: errorText,
          })
        } else {
          const visibleUntil = retryVisibleUntilRef.current.get(retryBannerId) ?? 0
          const now = Date.now()
          if (now >= visibleUntil) {
            setBanners((prev) => prev.filter((banner) => banner.id !== retryBannerId))
          } else {
            scheduleBannerRemoval(retryBannerId, visibleUntil - now)
          }
        }
        continue
      }

      if (msg.channel === 'session-control-response') {
        const parsed = parseJson<SessionControlResponse>(msg.payload ?? msg.body)
        if (parsed && parsed.ok === false && parsed.error) {
          addTransientErrorBanner('Session Control', parsed.error, 5)
        }
      }

      if (msg.channel === 'errors') {
        const parsed = parseJson<{ source?: string; error?: string }>(msg.payload ?? msg.body)
        const errorText = (parsed?.error || '').trim()
        if (errorText) {
          const source = (parsed?.source || '').replace(/^\[|\]$/g, '').trim() || 'System'
          addTransientErrorBanner(source, errorText, 8, `err-${source.toLowerCase()}-${msg.id}`)
        }
      }
    }
    processedMessageCountRef.current = messages.length + trimmedCount
  }, [addTransientErrorBanner, messages, scheduleBannerRemoval, trimmedCount, upsertBanner])

  const isRateLimited = banners.some((banner) => banner.kind === 'retry')

  React.useEffect(() => {
    if (!isRateLimited) return
    const timer = setInterval(() => {
      setBanners((prev) => {
        const next: UiBanner[] = []
        let changed = false
        for (const banner of prev) {
          if (banner.kind !== 'retry') {
            next.push(banner)
            continue
          }
          if (banner.countdown <= 1) {
            changed = true
            continue
          }
          changed = true
          next.push({ ...banner, countdown: banner.countdown - 1 })
        }
        return changed ? next : prev
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [isRateLimited])

  return { banners, isRateLimited, addTransientErrorBanner }
}
