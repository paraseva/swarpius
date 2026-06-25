import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  WebSocketContext,
  type SocketMessage,
  type WebSocketContextValue,
} from '../../websocketContext'
import { PrivacyTab } from './PrivacyTab'

const baseCtx: WebSocketContextValue = {
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

function renderTab(ctx: Partial<WebSocketContextValue> = {}) {
  return render(
    <WebSocketContext.Provider value={{ ...baseCtx, ...ctx }}>
      <PrivacyTab />
    </WebSocketContext.Provider>,
  )
}

afterEach(cleanup)

describe('PrivacyTab', () => {
  it('disables the clear button while a request is in flight', () => {
    renderTab({ isLlmActive: true })
    expect(screen.getByRole('button', { name: /clear conversation history/i }))
      .toBeDisabled()
  })

  it('requires confirmation before sending the clear request', async () => {
    const sendMessage = vi.fn(() => '')
    renderTab({ sendMessage })
    await userEvent.click(screen.getByRole('button', { name: /clear conversation history/i }))
    // Not sent yet — confirmation shown first.
    expect(sendMessage).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /yes, clear it/i }))
    expect(sendMessage).toHaveBeenCalledWith(
      'clear-conversation-request',
      expect.stringContaining('request_id'),
    )
  })

  it('sends the listening-history clear on the dedicated channel', async () => {
    const sendMessage = vi.fn(() => '')
    renderTab({ sendMessage })
    await userEvent.click(screen.getByRole('button', { name: /clear listening history/i }))
    await userEvent.click(screen.getByRole('button', { name: /yes, clear it/i }))
    expect(sendMessage).toHaveBeenCalledWith(
      'clear-listening-history-request',
      expect.stringContaining('request_id'),
    )
  })

  it('clears in an insecure context where crypto.randomUUID is unavailable', async () => {
    // Served over plain HTTP on a LAN IP, crypto.randomUUID (secure-context
    // only) is undefined — the clear must still send via the createUuid fallback.
    // randomUUID is inherited from Crypto.prototype, so shadow it with an own
    // property and delete that to restore (don't pollute later tests).
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      value: undefined, configurable: true,
    })
    try {
      const sendMessage = vi.fn(() => '')
      renderTab({ sendMessage })
      await userEvent.click(screen.getByRole('button', { name: /clear conversation history/i }))
      await userEvent.click(screen.getByRole('button', { name: /yes, clear it/i }))
      expect(sendMessage).toHaveBeenCalledWith(
        'clear-conversation-request',
        expect.stringContaining('request_id'),
      )
    } finally {
      Reflect.deleteProperty(globalThis.crypto, 'randomUUID')
    }
  })

  it('clears the local view when the server confirms', async () => {
    // Drive request_id determinism so the response matches.
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('11111111-1111-1111-1111-111111111111')
    const clearMessages = vi.fn()
    const sendMessage = vi.fn(() => '')
    const { rerender } = renderTab({ sendMessage, clearMessages })
    await userEvent.click(screen.getByRole('button', { name: /clear conversation history/i }))
    await userEvent.click(screen.getByRole('button', { name: /yes, clear it/i }))

    const response: SocketMessage = {
      id: 'r1',
      channel: 'clear-conversation-response',
      direction: 'inbound',
      body: '',
      payload: { request_id: '11111111-1111-1111-1111-111111111111', ok: true },
      timestamp: 1,
    }
    rerender(
      <WebSocketContext.Provider value={{ ...baseCtx, sendMessage, clearMessages, messages: [response] }}>
        <PrivacyTab />
      </WebSocketContext.Provider>,
    )
    expect(clearMessages).toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent(/cleared/i)
  })

  it('auto-dismisses the success message and restores the button after the timeout', () => {
    vi.useFakeTimers()
    try {
      vi.spyOn(crypto, 'randomUUID').mockReturnValue('11111111-1111-1111-1111-111111111111')
      const sendMessage = vi.fn(() => '')
      const { rerender } = renderTab({ sendMessage, clearMessages: vi.fn() })
      fireEvent.click(screen.getByRole('button', { name: /clear conversation history/i }))
      fireEvent.click(screen.getByRole('button', { name: /yes, clear it/i }))

      const response: SocketMessage = {
        id: 'r1',
        channel: 'clear-conversation-response',
        direction: 'inbound',
        body: '',
        payload: { request_id: '11111111-1111-1111-1111-111111111111', ok: true },
        timestamp: 1,
      }
      rerender(
        <WebSocketContext.Provider value={{ ...baseCtx, sendMessage, messages: [response] }}>
          <PrivacyTab />
        </WebSocketContext.Provider>,
      )
      expect(screen.getByRole('status')).toHaveTextContent(/cleared/i)

      act(() => { vi.advanceTimersByTime(5000) })

      expect(screen.queryByRole('status')).toBeNull()
      expect(
        screen.getByRole('button', { name: /clear conversation history/i }),
      ).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})
