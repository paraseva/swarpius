/**
 * Tests for the WS URL helpers in config.ts.
 *
 * TTS URL is derived from the chat WS URL — same host, same port,
 * /tts path instead of /ws. This pins that derivation independent
 * of which transport (loopback rewrite, https→wss, etc.) the chat
 * URL was resolved through.
 */
import { describe, it, expect } from 'vitest'
import { deriveTtsWebSocketUrl } from './config'

describe('deriveTtsWebSocketUrl', () => {
  it('swaps /ws for /tts on the chat URL', () => {
    expect(deriveTtsWebSocketUrl('ws://localhost:8080/ws')).toBe(
      'ws://localhost:8080/tts',
    )
  })

  it('preserves wss for HTTPS deployments', () => {
    expect(deriveTtsWebSocketUrl('wss://swarpius.example.com/ws')).toBe(
      'wss://swarpius.example.com/tts',
    )
  })

  it('preserves the port', () => {
    expect(deriveTtsWebSocketUrl('ws://192.168.1.50:8080/ws')).toBe(
      'ws://192.168.1.50:8080/tts',
    )
  })

  it('handles URL with query string on chat path', () => {
    expect(
      deriveTtsWebSocketUrl('ws://localhost:8080/ws?session=abc'),
    ).toBe('ws://localhost:8080/tts')
  })

  it('drops trailing slash on /ws/ canonical form', () => {
    expect(deriveTtsWebSocketUrl('ws://localhost:8080/ws/')).toBe(
      'ws://localhost:8080/tts',
    )
  })

  it('returns empty string for empty input (TTS disabled)', () => {
    expect(deriveTtsWebSocketUrl('')).toBe('')
  })

  it('returns empty string for malformed input', () => {
    expect(deriveTtsWebSocketUrl('not-a-url')).toBe('')
  })
})
