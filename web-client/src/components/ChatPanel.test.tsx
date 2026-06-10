import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  WebSocketContext,
  type SocketMessage,
  type WebSocketContextValue,
} from '../websocketContext'
import { ChatPanel } from './ChatPanel'

// Mock TTS module to avoid side effects
vi.mock('../tts', () => ({
  playServerTts: vi.fn(),
  TTS_ERROR_EVENT_NAME: 'tts-error',
}))

class MockSpeechRecognition {
  continuous = false
  interimResults = false
  lang = ''
  onresult: ((event: unknown) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onend: (() => void) | null = null
  start = vi.fn()
  stop = vi.fn()
  abort = vi.fn()
}

const defaultCtx: WebSocketContextValue = {
  status: 'open',
  messages: [],
  sendMessage: () => '',
  isLlmActive: false,
  latestZoneSnapshot: null,
    connectionGeneration: 0,
    isRestarting: false,
    markRestarting: () => {},
  trimmedCount: 0,
}

function renderChatPanel(ctx: Partial<WebSocketContextValue> = {}) {
  return render(
    <WebSocketContext.Provider value={{ ...defaultCtx, ...ctx }}>
      <ChatPanel isAutoTtsEnabled={false} isDevMode={false} ttsWsUrl="" />
    </WebSocketContext.Provider>,
  )
}

describe('ChatPanel speech input', () => {
  let originalSR: unknown

  beforeEach(() => {
    originalSR = (globalThis as Record<string, unknown>).webkitSpeechRecognition
  })

  afterEach(() => {
    cleanup()
    if (originalSR === undefined) {
      delete (globalThis as Record<string, unknown>).webkitSpeechRecognition
    } else {
      ;(globalThis as Record<string, unknown>).webkitSpeechRecognition = originalSR
    }
  })

  it('shows mic button when Web Speech API is available', () => {
    ;(globalThis as Record<string, unknown>).webkitSpeechRecognition = MockSpeechRecognition
    renderChatPanel()
    expect(screen.getByRole('button', { name: 'Start speech input' })).toBeInTheDocument()
  })

  it('disables (rather than hides) the mic button when Web Speech API is unavailable', () => {
    delete (globalThis as Record<string, unknown>).webkitSpeechRecognition
    delete (globalThis as Record<string, unknown>).SpeechRecognition
    renderChatPanel()
    const micButton = screen.getByRole('button', {
      name: 'Voice input is not supported in this browser',
    })
    expect(micButton).toBeDisabled()
    // Tooltip is on the wrapper, not the disabled button (which
    // wouldn't fire hover).
    expect(screen.getByTitle(/not supported in this browser/i)).toBeInTheDocument()
  })

  it('toggles to listening state on mic button click', async () => {
    ;(globalThis as Record<string, unknown>).webkitSpeechRecognition = MockSpeechRecognition
    renderChatPanel()
    const user = userEvent.setup()

    const micButton = screen.getByRole('button', { name: 'Start speech input' })
    await user.click(micButton)

    expect(screen.getByRole('button', { name: 'Stop listening' })).toBeInTheDocument()
  })
})

describe('ChatPanel directive outbounds', () => {
  afterEach(() => {
    cleanup()
  })

  it('marks acknowledged keyword outbounds with the directive class', () => {
    const messages: SocketMessage[] = [
      {
        id: 'm-stop', channel: 'chat', direction: 'outbound',
        body: 'stop', payload: { body: 'stop' },
        timestamp: Date.now(),
      },
      {
        id: 'evt-ack', channel: 'agent-outputs', direction: 'inbound',
        body: '',
        payload: {
          event_type: 'control_command_acknowledged',
          client_msg_id: 'm-stop',
          action: 'interrupt_only',
        },
        timestamp: Date.now(),
      },
    ]
    const { container } = renderChatPanel({ messages })

    const item = container.querySelector('li[data-directive="true"]')
    expect(item).not.toBeNull()
    expect(item?.textContent).toContain('stop')
  })

  it('does not mark a plain outbound as a directive', () => {
    const messages: SocketMessage[] = [
      {
        id: 'm-play', channel: 'chat', direction: 'outbound',
        body: 'play some music', payload: { body: 'play some music' },
        timestamp: Date.now(),
      },
    ]
    const { container } = renderChatPanel({ messages })

    expect(container.querySelector('li[data-directive="true"]')).toBeNull()
  })

  it('keeps message-outbound on directive items so they read as user-issued', () => {
    const messages: SocketMessage[] = [
      {
        id: 'm-stop', channel: 'chat', direction: 'outbound',
        body: 'stop', payload: { body: 'stop' },
        timestamp: Date.now(),
      },
      {
        id: 'evt-ack', channel: 'agent-outputs', direction: 'inbound',
        body: '',
        payload: {
          event_type: 'control_command_acknowledged',
          client_msg_id: 'm-stop',
          action: 'interrupt_only',
        },
        timestamp: Date.now(),
      },
    ]
    const { container } = renderChatPanel({ messages })

    const item = container.querySelector('li[data-directive="true"]') as HTMLElement
    expect(item).not.toBeNull()
    expect(item.classList.contains('message-directive')).toBe(true)
    expect(item.classList.contains('message-outbound')).toBe(true)
  })

  it('marks replayed directive outbounds by persisted client_msg_id', () => {
    // Replayed outbound gets a fresh local id; the original
    // client_msg_id is in meta. The lookup must use the meta value.
    const messages: SocketMessage[] = [
      {
        id: 'fresh-replay-uuid', channel: 'chat', direction: 'outbound',
        body: 'stop', payload: { body: 'stop' },
        timestamp: Date.now(),
        meta: { replay: true, direction: 'outbound', client_msg_id: 'm-stop' },
      },
      {
        id: 'evt-ack', channel: 'agent-outputs', direction: 'inbound',
        body: '',
        payload: {
          event_type: 'control_command_acknowledged',
          client_msg_id: 'm-stop',
          action: 'interrupt_only',
        },
        timestamp: Date.now(),
      },
    ]
    const { container } = renderChatPanel({ messages })

    const item = container.querySelector('li[data-directive="true"]')
    expect(item).not.toBeNull()
  })

  it('does not render a request-id badge for directive outbounds in dev mode', () => {
    const messages: SocketMessage[] = [
      {
        id: 'm-stop', channel: 'chat', direction: 'outbound',
        body: 'stop', payload: { body: 'stop' }, timestamp: Date.now(),
      },
      {
        id: 'evt-ack', channel: 'agent-outputs', direction: 'inbound',
        body: '',
        payload: {
          event_type: 'control_command_acknowledged',
          client_msg_id: 'm-stop', action: 'interrupt_only',
        },
        timestamp: Date.now(),
      },
      {
        id: 'evt-rid', channel: 'agent-outputs', direction: 'inbound',
        body: '',
        payload: {
          event_type: 'request_id_assignment',
          request_id: 'rq-c01-0001',
          client_msg_id: 'm-stop',
        },
        timestamp: Date.now(),
      },
    ]
    const { container } = render(
      <WebSocketContext.Provider value={{ ...defaultCtx, messages }}>
        <ChatPanel isAutoTtsEnabled={false} isDevMode={true} ttsWsUrl="" />
      </WebSocketContext.Provider>,
    )

    const item = container.querySelector('li[data-directive="true"]')
    expect(item).not.toBeNull()
    expect(item?.textContent ?? '').not.toContain('rq-c01-0001')
  })
})

describe('ChatPanel empty-state prompt chips', () => {
  afterEach(() => {
    cleanup()
  })

  const makeOutboundChatMessage = (): SocketMessage => ({
    id: 'm-1',
    channel: 'chat',
    direction: 'outbound',
    body: 'hello',
    payload: { body: 'hello' },
    timestamp: Date.now(),
  })

  it('renders prompt chips when the chat has no messages', () => {
    renderChatPanel()
    expect(
      screen.getByRole('button', { name: /Play the most popular UK song from 1976/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /Queue a couple of jazz albums on the default zone/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /Play 20 random tracks from all my Pink Floyd albums/i }),
    ).toBeInTheDocument()
  })

  it('hides prompt chips once a chat message exists', () => {
    renderChatPanel({ messages: [makeOutboundChatMessage()] })
    expect(
      screen.queryByRole('button', { name: /Play the most popular UK song from 1976/i }),
    ).toBeNull()
  })

  it('populates the composer with the chip prompt when clicked', async () => {
    renderChatPanel()
    const user = userEvent.setup()
    const chip = screen.getByRole('button', {
      name: /Play 20 random tracks from all my Pink Floyd albums/i,
    })
    await user.click(chip)
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(textarea.value).toBe('Play 20 random tracks from all my Pink Floyd albums')
  })
})

describe('ChatPanel handleSubmit', () => {
  afterEach(cleanup)

  it('sends a trimmed non-empty draft on Enter and clears the composer', async () => {
    const sendMessage = vi.fn(() => 'rq-1')
    renderChatPanel({ sendMessage })
    const user = userEvent.setup()
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    await user.type(box, '  play jazz  ')
    await user.keyboard('{Enter}')
    expect(sendMessage).toHaveBeenCalledWith('chat', 'play jazz')
    expect(box.value).toBe('')
  })

  it('does not send a whitespace-only draft', async () => {
    const sendMessage = vi.fn(() => '')
    renderChatPanel({ sendMessage })
    const user = userEvent.setup()
    await user.type(screen.getByRole('textbox'), '   ')
    await user.keyboard('{Enter}')
    expect(sendMessage).not.toHaveBeenCalled()
  })

  it('inserts a newline on Shift+Enter without sending', async () => {
    const sendMessage = vi.fn(() => '')
    renderChatPanel({ sendMessage })
    const user = userEvent.setup()
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    await user.type(box, 'line one')
    await user.keyboard('{Shift>}{Enter}{/Shift}')
    expect(sendMessage).not.toHaveBeenCalled()
    expect(box.value).toContain('\n')
  })
})
