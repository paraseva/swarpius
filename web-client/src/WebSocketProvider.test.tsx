import React from 'react'
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WebSocketProvider } from './WebSocketProvider'
import { useWebSocket } from './websocketContext'

class MockWebSocket {
  static instances: MockWebSocket[] = []

  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  readyState = MockWebSocket.CONNECTING
  url: string
  listeners = new Map<string, ((e: Event) => void)[]>()

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  addEventListener(type: string, fn: (e: Event) => void) {
    const list = this.listeners.get(type) ?? []
    list.push(fn)
    this.listeners.set(type, list)
  }

  removeEventListener(type: string, fn: (e: Event) => void) {
    const list = this.listeners.get(type) ?? []
    this.listeners.set(type, list.filter((h) => h !== fn))
  }

  send() {}

  close() {
    this.readyState = MockWebSocket.CLOSED
  }

  fireOpen() {
    this.readyState = MockWebSocket.OPEN
    for (const handler of this.listeners.get('open') ?? []) handler(new Event('open'))
  }

  fireMessage(value: unknown) {
    const data = typeof value === 'string' ? value : JSON.stringify(value)
    const event = new MessageEvent('message', { data })
    for (const handler of this.listeners.get('message') ?? []) handler(event)
  }

  fireClose(code: number) {
    this.readyState = MockWebSocket.CLOSED
    const event = new CloseEvent('close', { code })
    for (const handler of this.listeners.get('close') ?? []) handler(event)
  }

  fireError() {
    for (const handler of this.listeners.get('error') ?? []) handler(new Event('error'))
  }
}

let probeValue: ReturnType<typeof useWebSocket> | null = null
const Probe = () => {
  const ctx = useWebSocket()
  React.useEffect(() => {
    probeValue = ctx
  })
  return null
}

const setup = () => {
  MockWebSocket.instances = []
  probeValue = null
  vi.stubGlobal('WebSocket', MockWebSocket)
  const rendered = render(
    <WebSocketProvider>
      <Probe />
    </WebSocketProvider>,
  )
  const socket = MockWebSocket.instances[0]
  act(() => socket.fireOpen())
  return { rendered, socket }
}

describe('WebSocketProvider — message routing', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('stores non-snapshot messages in the messages history', () => {
    const { socket } = setup()

    act(() => {
      socket.fireMessage({ channel: 'chat', body: 'hello' })
      socket.fireMessage({ channel: 'agent-outputs', body: '{"x": 1}' })
    })

    expect(probeValue!.messages.map((m) => m.channel)).toEqual(['chat', 'agent-outputs'])
  })

  it('does not store zone-snapshots in the messages history', () => {
    const { socket } = setup()

    act(() => {
      socket.fireMessage({
        channel: 'zone-snapshots',
        payload: { source: '[Roon snapshot]', data: { zones: [], timestamp_ms: 1 } },
      })
      socket.fireMessage({
        channel: 'zone-snapshots',
        payload: { source: '[Roon snapshot]', data: { zones: [], timestamp_ms: 2 } },
      })
    })

    expect(probeValue!.messages).toEqual([])
  })

  it('exposes the latest snapshot via latestZoneSnapshot and overwrites on each event', () => {
    const { socket } = setup()

    expect(probeValue!.latestZoneSnapshot).toBeNull()

    const first = { source: '[Roon snapshot]', data: { zones: [{ zone_id: 'z1' }], timestamp_ms: 1 } }
    act(() => {
      socket.fireMessage({ channel: 'zone-snapshots', payload: first })
    })
    expect(probeValue!.latestZoneSnapshot).toEqual(first)

    const second = { source: '[Roon snapshot]', data: { zones: [{ zone_id: 'z2' }], timestamp_ms: 2 } }
    act(() => {
      socket.fireMessage({ channel: 'zone-snapshots', payload: second })
    })
    expect(probeValue!.latestZoneSnapshot).toEqual(second)
  })

  it('keeps non-snapshot traffic flowing while snapshots take the latest-only path', () => {
    const { socket } = setup()

    act(() => {
      socket.fireMessage({ channel: 'chat', body: 'before' })
      socket.fireMessage({
        channel: 'zone-snapshots',
        payload: { source: '[Roon snapshot]', data: { zones: [], timestamp_ms: 1 } },
      })
      socket.fireMessage({ channel: 'chat', body: 'after' })
    })

    expect(probeValue!.messages.map((m) => m.channel)).toEqual(['chat', 'chat'])
    expect(probeValue!.latestZoneSnapshot).not.toBeNull()
  })

  it('clearMessages wipes conversation content but keeps connection state', () => {
    // Conversation content (chat + diagnostics) is cleared; connection state the
    // server only re-sends on (re)connect — feature-availability, roon-core-status
    // — is kept, so a local clear (no reconnect) doesn't strand overlay state.
    const { socket } = setup()

    act(() => {
      socket.fireMessage({ channel: 'feature-availability', payload: { is_bundle: false } })
      socket.fireMessage({ channel: 'roon-core-status', payload: { state: 'connected' } })
      socket.fireMessage({ channel: 'chat', body: 'hello' })
      socket.fireMessage({ channel: 'agent-outputs', body: '{"x":1}' })
    })
    expect(probeValue!.messages.map((m) => m.channel)).toEqual(
      ['feature-availability', 'roon-core-status', 'chat', 'agent-outputs'],
    )

    act(() => probeValue!.clearMessages?.())
    expect(probeValue!.messages.map((m) => m.channel)).toEqual(
      ['feature-availability', 'roon-core-status'],
    )
  })
})

describe('WebSocketProvider — active-call (Thinking) tracking', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('a live call_started activates and call_completed deactivates', () => {
    const { socket } = setup()

    act(() => socket.fireMessage({
      channel: 'llm-diagnostics', payload: { event_type: 'call_started', call_id: 'c1' },
    }))
    expect(probeValue!.isLlmActive).toBe(true)

    act(() => socket.fireMessage({
      channel: 'llm-diagnostics', payload: { event_type: 'call_completed', call_id: 'c1' },
    }))
    expect(probeValue!.isLlmActive).toBe(false)
  })

  it('replayed (historical) call events do not activate — no phantom Thinking bubble', () => {
    const { socket } = setup()

    act(() => socket.fireMessage({
      channel: 'llm-diagnostics',
      payload: { event_type: 'call_started', call_id: 'old1' },
      meta: { historical: true },
    }))
    expect(probeValue!.isLlmActive).toBe(false)
  })
})

describe('WebSocketProvider — history-cursor batch signalling', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('records reached-beginning and bumps the batch token per channel', () => {
    const { socket } = setup()

    act(() => socket.fireMessage({
      channel: 'history-cursor', payload: { channel: 'chat', has_older: false },
    }))
    expect(probeValue!.reachedBeginningByChannel?.get('chat')).toBe(true)
    expect(probeValue!.historyBatchTokenByChannel?.get('chat')).toBe(1)

    // Next batch for the same channel: older history still remains, token advances.
    act(() => socket.fireMessage({
      channel: 'history-cursor', payload: { channel: 'chat', has_older: true },
    }))
    expect(probeValue!.reachedBeginningByChannel?.get('chat')).toBe(false)
    expect(probeValue!.historyBatchTokenByChannel?.get('chat')).toBe(2)
  })

  it('tracks channels independently', () => {
    const { socket } = setup()

    act(() => {
      socket.fireMessage({ channel: 'history-cursor', payload: { channel: 'chat', has_older: true } })
      socket.fireMessage({ channel: 'history-cursor', payload: { channel: 'agent-outputs', has_older: false } })
    })

    expect(probeValue!.reachedBeginningByChannel?.get('chat')).toBe(false)
    expect(probeValue!.reachedBeginningByChannel?.get('agent-outputs')).toBe(true)
    expect(probeValue!.historyBatchTokenByChannel?.get('chat')).toBe(1)
    expect(probeValue!.historyBatchTokenByChannel?.get('agent-outputs')).toBe(1)
  })

  it('ignores a channel-less cursor (the connect replay sends one)', () => {
    const { socket } = setup()

    act(() => socket.fireMessage({
      channel: 'history-cursor', payload: { has_older: false },
    }))
    expect(probeValue!.reachedBeginningByChannel?.get('chat')).toBeUndefined()
  })

  it('a malicious cursor channel name does not pollute Object.prototype', () => {
    const { socket } = setup()

    act(() => socket.fireMessage({
      channel: 'history-cursor', payload: { channel: '__proto__', has_older: false },
    }))
    expect(Object.getPrototypeOf({})).toBe(Object.prototype)
    expect(Object.keys(Object.prototype)).toEqual([])
  })
})

describe('WebSocketProvider — connection lifecycle', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('session takeover (4001) sets taken_over and does not reconnect', () => {
    const { socket } = setup()
    vi.useFakeTimers()
    const before = MockWebSocket.instances.length
    act(() => socket.fireClose(4001))
    expect(probeValue!.status).toBe('taken_over')
    act(() => vi.advanceTimersByTime(5000))
    expect(MockWebSocket.instances.length).toBe(before)
  })

  it('an ordinary close sets closed and schedules a reconnect', () => {
    const { socket } = setup()
    vi.useFakeTimers()
    const before = MockWebSocket.instances.length
    act(() => socket.fireClose(1006))
    expect(probeValue!.status).toBe('closed')
    act(() => vi.advanceTimersByTime(5000))
    expect(MockWebSocket.instances.length).toBeGreaterThan(before)
  })

  it('a socket error surfaces error status', () => {
    const { socket } = setup()
    vi.useFakeTimers()
    act(() => socket.fireError())
    expect(probeValue!.status).toBe('error')
  })
})
