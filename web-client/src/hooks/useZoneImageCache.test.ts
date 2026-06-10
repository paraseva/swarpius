import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, type Mock } from 'vitest'
import { useZoneImageCache } from './useZoneImageCache'
import {
  DEFAULT_ART_HEIGHT,
  DEFAULT_ART_WIDTH,
} from '../components/zoneStatusModel'
import { imageCacheKey } from '../components/zoneStatusUtils'
import type { SocketMessage } from '../websocketContext'

type SendMock = Mock<(channel: string, body: string) => string>
const makeSend = (): SendMock => vi.fn<(channel: string, body: string) => string>(() => 'rq-id')

let nextId = 0
const inboundImageResponse = (payload: object): SocketMessage => ({
  id: `m${++nextId}`,
  channel: 'roon-image-response',
  direction: 'inbound',
  body: JSON.stringify(payload),
  payload,
  timestamp: Date.now(),
})

const W = DEFAULT_ART_WIDTH
const H = DEFAULT_ART_HEIGHT

const makeArgs = (overrides: Partial<Parameters<typeof useZoneImageCache>[0]> = {}) => ({
  messages: [] as SocketMessage[],
  trimmedCount: 0,
  sendMessage: makeSend(),
  onImageFailure: vi.fn(),
  onDefaultArtReady: vi.fn(),
  resolveExpandRequest: vi.fn(),
  resetToken: 0,
  ...overrides,
})

describe('useZoneImageCache', () => {
  beforeEach(() => {
    nextId = 0
  })

  it('lookup returns undefined when key is not cached', () => {
    const { result } = renderHook(() => useZoneImageCache(makeArgs()))
    expect(result.current.lookup('img-x', W, H)).toBeUndefined()
  })

  it('requestIfMissing dispatches roon-image-request once and dedupes a second call', () => {
    const sendMessage = makeSend()
    const { result, rerender } = renderHook(
      (args: Parameters<typeof useZoneImageCache>[0]) => useZoneImageCache(args),
      { initialProps: makeArgs({ sendMessage }) },
    )

    act(() => result.current.requestIfMissing('img-x', W, H))
    act(() => result.current.requestIfMissing('img-x', W, H))
    rerender(makeArgs({ sendMessage }))
    act(() => result.current.requestIfMissing('img-x', W, H))

    expect(sendMessage).toHaveBeenCalledTimes(1)
    const firstCall = sendMessage.mock.calls[0]
    if (!firstCall) throw new Error('sendMessage was not called')
    expect(firstCall[0]).toBe('roon-image-request')
    const parsed = JSON.parse(firstCall[1]) as Record<string, unknown>
    expect(parsed.image_key).toBe('img-x')
    expect(parsed.width).toBe(W)
    expect(parsed.height).toBe(H)
  })

  it('caches a successful response and surfaces it via lookup, plus onDefaultArtReady for default size', () => {
    const onDefaultArtReady = vi.fn()
    const onImageFailure = vi.fn()
    const resolveExpandRequest = vi.fn()
    const { result, rerender } = renderHook(
      (args: Parameters<typeof useZoneImageCache>[0]) => useZoneImageCache(args),
      { initialProps: makeArgs({ onDefaultArtReady, onImageFailure, resolveExpandRequest }) },
    )

    const messages = [inboundImageResponse({
      ok: true,
      image_key: 'img-x',
      width: W,
      height: H,
      mime_type: 'image/jpeg',
      base64_data: 'AAAA',
    })]
    rerender(makeArgs({ messages, onDefaultArtReady, onImageFailure, resolveExpandRequest }))

    const expectedDataUri = 'data:image/jpeg;base64,AAAA'
    expect(result.current.lookup('img-x', W, H)).toBe(expectedDataUri)
    expect(onDefaultArtReady).toHaveBeenCalledWith('img-x', expectedDataUri)
    expect(resolveExpandRequest).toHaveBeenCalledWith(imageCacheKey('img-x', W, H), expectedDataUri)
    expect(onImageFailure).not.toHaveBeenCalled()
  })

  it('does not call onDefaultArtReady for non-default sizes', () => {
    const onDefaultArtReady = vi.fn()
    const { rerender } = renderHook(
      (args: Parameters<typeof useZoneImageCache>[0]) => useZoneImageCache(args),
      { initialProps: makeArgs({ onDefaultArtReady }) },
    )

    rerender(makeArgs({
      onDefaultArtReady,
      messages: [inboundImageResponse({
        ok: true,
        image_key: 'img-x',
        width: 1024,
        height: 1024,
        mime_type: 'image/jpeg',
        base64_data: 'AAAA',
      })],
    }))

    expect(onDefaultArtReady).not.toHaveBeenCalled()
  })

  it('routes a failure response to onImageFailure with the image_key', () => {
    const onImageFailure = vi.fn()
    const { rerender } = renderHook(
      (args: Parameters<typeof useZoneImageCache>[0]) => useZoneImageCache(args),
      { initialProps: makeArgs({ onImageFailure }) },
    )

    rerender(makeArgs({
      onImageFailure,
      messages: [inboundImageResponse({ ok: false, image_key: 'img-broken', error: 'not found' })],
    }))

    expect(onImageFailure).toHaveBeenCalledWith('img-broken')
  })

  it('LRU-evicts the oldest entry when capacity (50) is exceeded', () => {
    const { result, rerender } = renderHook(
      (args: Parameters<typeof useZoneImageCache>[0]) => useZoneImageCache(args),
      { initialProps: makeArgs() },
    )

    // Push 51 successful responses — first one should be evicted.
    const messages: SocketMessage[] = Array.from({ length: 51 }, (_, i) =>
      inboundImageResponse({
        ok: true,
        image_key: `img-${i}`,
        width: W,
        height: H,
        mime_type: 'image/png',
        base64_data: `B${i}`,
      }),
    )
    rerender(makeArgs({ messages }))

    expect(result.current.lookup('img-0', W, H)).toBeUndefined()
    expect(result.current.lookup('img-50', W, H)).toBe('data:image/png;base64,B50')
  })

  it('clearing pendingImageRequests on resetToken change allows a re-request', () => {
    const sendMessage = makeSend()
    const { result, rerender } = renderHook(
      (args: Parameters<typeof useZoneImageCache>[0]) => useZoneImageCache(args),
      { initialProps: makeArgs({ sendMessage, resetToken: 0 }) },
    )

    act(() => result.current.requestIfMissing('img-x', W, H))
    expect(sendMessage).toHaveBeenCalledTimes(1)

    rerender(makeArgs({ sendMessage, resetToken: 1 }))
    act(() => result.current.requestIfMissing('img-x', W, H))

    expect(sendMessage).toHaveBeenCalledTimes(2)
  })
})
