/**
 * useChatTtsAutoPlay — auto-speaks the latest inbound assistant chat.
 *
 * Contract (from the auto-TTS design): a new inbound chat message is
 * spoken (meta.speak_text overrides the body); nothing is spoken when
 * auto-TTS is disabled or the message is outbound; and a TTS error event
 * raises a transient 'TTS' banner.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi, type Mock } from 'vitest'

vi.mock('../tts', () => ({
  playServerTts: vi.fn(() => Promise.resolve()),
  TTS_ERROR_EVENT_NAME: 'tts-error',
}))

import { playServerTts, TTS_ERROR_EVENT_NAME } from '../tts'
import { useChatTtsAutoPlay } from './useChatTtsAutoPlay'
import type { SocketMessage } from '../websocketContext'

const msg = (over: Partial<SocketMessage>): SocketMessage => ({
  id: 'm1',
  channel: 'chat',
  direction: 'inbound',
  body: 'hi',
  timestamp: 1,
  ...over,
})

const opts = (over = {}) => ({
  isAutoTtsEnabled: true,
  ttsHealth: 'healthy' as const,
  ttsWsUrl: 'ws://tts',
  addTransientErrorBanner: vi.fn(),
  ...over,
})

const spoken = () => (playServerTts as Mock).mock.calls.map((c) => c[0])

describe('useChatTtsAutoPlay', () => {
  afterEach(() => vi.clearAllMocks())

  it('speaks a new inbound message, with meta.speak_text overriding the body', async () => {
    const o = opts()
    const { rerender } = renderHook(
      ({ messages }) => useChatTtsAutoPlay(messages, o),
      { initialProps: { messages: [] as SocketMessage[] } },
    )
    rerender({ messages: [msg({ meta: { speak_text: 'Hello there' } })] })
    await waitFor(() => expect(playServerTts).toHaveBeenCalled())
    expect(spoken()[0]).toContain('Hello there')
  })

  it('does not speak when auto-TTS is disabled', async () => {
    const o = opts({ isAutoTtsEnabled: false })
    const { rerender } = renderHook(
      ({ messages }) => useChatTtsAutoPlay(messages, o),
      { initialProps: { messages: [] as SocketMessage[] } },
    )
    rerender({ messages: [msg({ meta: { speak_text: 'Hello' } })] })
    await new Promise((r) => setTimeout(r, 0))
    expect(playServerTts).not.toHaveBeenCalled()
  })

  it('does not speak an outbound (user) message', async () => {
    const o = opts()
    const { rerender } = renderHook(
      ({ messages }) => useChatTtsAutoPlay(messages, o),
      { initialProps: { messages: [] as SocketMessage[] } },
    )
    rerender({ messages: [msg({ direction: 'outbound', body: 'play jazz' })] })
    await new Promise((r) => setTimeout(r, 0))
    expect(playServerTts).not.toHaveBeenCalled()
  })

  it('raises a transient TTS banner on a TTS error event', () => {
    const o = opts()
    renderHook(() => useChatTtsAutoPlay([], o))
    window.dispatchEvent(new Event(TTS_ERROR_EVENT_NAME))
    expect(o.addTransientErrorBanner).toHaveBeenCalledWith(
      'TTS', expect.any(String), expect.any(Number),
    )
  })
})
