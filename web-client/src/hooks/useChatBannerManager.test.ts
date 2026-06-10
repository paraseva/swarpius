import { act, renderHook } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import type { SocketMessage } from '../websocketContext'
import { useChatBannerManager } from './useChatBannerManager'

function errorMsg(
  id: string,
  body: { source?: string; error?: string },
  meta: Record<string, unknown> | undefined = undefined,
): SocketMessage {
  return {
    id,
    channel: 'errors',
    direction: 'inbound',
    body: '',
    payload: body,
    timestamp: 0,
    meta,
  }
}

describe('useChatBannerManager', () => {
  it('raises a banner for a live error event', () => {
    const messages = [errorMsg('e1', { source: '[Request]', error: 'boom' })]
    const { result } = renderHook(() => useChatBannerManager(messages, 0))
    expect(result.current.banners.length).toBe(1)
    expect(result.current.banners[0].error).toBe('boom')
  })

  it('does not raise a banner for a replayed error event', () => {
    const messages = [
      errorMsg('e1', { source: '[Request]', error: 'boom' }, { replay: true }),
    ]
    const { result } = renderHook(() => useChatBannerManager(messages, 0))
    expect(result.current.banners).toEqual([])
  })

  it('does not raise a banner for a replayed rate-limit retry', () => {
    const messages: SocketMessage[] = [
      {
        id: 'r1',
        channel: 'rate-limit',
        direction: 'inbound',
        body: '',
        payload: {
          active: true,
          retriable: true,
          retry_in_seconds: 30,
          attempt: 1,
          max_retries: 3,
          agent_name: 'Coordinator',
          error: 'throttled',
        },
        timestamp: 0,
        meta: { replay: true },
      },
    ]
    const { result } = renderHook(() => useChatBannerManager(messages, 0))
    expect(result.current.banners).toEqual([])
    expect(result.current.isRateLimited).toBe(false)
  })
})

function rateLimitMsg(over: Record<string, unknown> = {}): SocketMessage {
  return {
    id: 'r1',
    channel: 'rate-limit',
    direction: 'inbound',
    body: '',
    payload: {
      active: true,
      retriable: true,
      retry_in_seconds: 30,
      attempt: 1,
      max_retries: 3,
      agent_name: 'Coordinator',
      error: 'throttled',
      ...over,
    },
    timestamp: 0,
  }
}

describe('useChatBannerManager — rate limit + transient errors', () => {
  it('raises a retry banner for an active retriable rate-limit', () => {
    const { result } = renderHook(() => useChatBannerManager([rateLimitMsg()], 0))
    expect(result.current.isRateLimited).toBe(true)
    expect(result.current.banners.some((b) => b.kind === 'retry')).toBe(true)
  })

  it('raises an error (not retry) banner for a non-retriable rate-limit', () => {
    const { result } = renderHook(
      () => useChatBannerManager([rateLimitMsg({ retriable: false })], 0),
    )
    expect(result.current.banners.some((b) => b.kind === 'error')).toBe(true)
    expect(result.current.banners.some((b) => b.kind === 'retry')).toBe(false)
  })

  it('addTransientErrorBanner pushes an error banner for the agent', () => {
    const { result } = renderHook(() => useChatBannerManager([], 0))
    act(() => result.current.addTransientErrorBanner('Coordinator', 'Boom', 5))
    expect(result.current.banners).toHaveLength(1)
    expect(result.current.banners[0]).toMatchObject({
      kind: 'error',
      agentName: 'Coordinator',
    })
  })
})
