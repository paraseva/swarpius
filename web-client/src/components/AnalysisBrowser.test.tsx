/**
 * Frontend contract tests for AnalysisBrowser.
 *
 *   - WS runtime validation: malformed payloads log a console warning
 *     and are dropped at the boundary.
 *   - Request debouncing: rapid clicks on Scan must not fire duplicate
 *     analysis-run-requests, even when React hasn't re-rendered the
 *     disabled state.
 *   - Accessibility: the dispute form's select + textarea have
 *     associated <label>s; Escape closes the form.
 */

import * as React from 'react'
import { cleanup, render, screen, act, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  WebSocketContext,
  type SocketMessage,
  type WebSocketContextValue,
} from '../websocketContext'
import { AnalysisBrowser } from './AnalysisBrowser'

function makeCtx(overrides: Partial<WebSocketContextValue> = {}): WebSocketContextValue {
  return {
    status: 'open',
    messages: [],
    sendMessage: vi.fn(() => 'fake-rid'),
    isLlmActive: false,
    latestZoneSnapshot: null,
    connectionGeneration: 0,
    isRestarting: false,
    markRestarting: () => {},
    trimmedCount: 0,
    ...overrides,
  }
}

function renderBrowser(ctx: Partial<WebSocketContextValue> = {}) {
  const value = makeCtx(ctx)
  const utils = render(
    <WebSocketContext.Provider value={value}>
      <AnalysisBrowser />
    </WebSocketContext.Provider>,
  )
  return { ...utils, sendMessage: value.sendMessage as ReturnType<typeof vi.fn> }
}

/** A harness with a controlled `messages` array so the test can push
 * inbound messages after mount — needed to drive AnalysisBrowser through
 * the real list → click → detail flow. `sendMessage` is hoisted so its
 * mock.calls persist across re-renders.
 */
function renderWithControlledMessages() {
  const sendMessage = vi.fn(() => 'fake-rid')
  const control: { push: (msg: SocketMessage) => void } = {
    push: () => { throw new Error('Harness not mounted') },
  }

  function Harness() {
    const [messages, setMessages] = React.useState<SocketMessage[]>([])
    React.useEffect(() => {
      control.push = (msg) => setMessages((prev) => [...prev, msg])
    }, [])
    const value: WebSocketContextValue = {
      status: 'open',
      messages,
      sendMessage,
      isLlmActive: false,
      latestZoneSnapshot: null,
    connectionGeneration: 0,
    isRestarting: false,
    markRestarting: () => {},
      trimmedCount: 0,
    }
    return (
      <WebSocketContext.Provider value={value}>
        <AnalysisBrowser />
      </WebSocketContext.Provider>
    )
  }

  const utils = render(<Harness />)
  return {
    ...utils,
    sendMessage: sendMessage as ReturnType<typeof vi.fn>,
    pushInbound: (msg: SocketMessage) => control.push(msg),
  }
}

function makeInbound(channel: string, payload: unknown): SocketMessage {
  return {
    id: `msg-${Math.random().toString(36).slice(2)}`,
    channel,
    direction: 'inbound',
    body: '',
    payload,
    timestamp: Date.now(),
  } as SocketMessage
}

/** Extract the `request_id` from the last outbound sendMessage call on
 * the given channel. Throws if none found — the caller expected an
 * in-flight request the UI should have initiated. */
function lastRequestIdFor(sendMessage: ReturnType<typeof vi.fn>, channel: string): string {
  const call = [...sendMessage.mock.calls].reverse().find((c) => c[0] === channel)
  if (!call) throw new Error(`no sendMessage call for channel ${channel}`)
  return JSON.parse(call[1] as string).request_id as string
}

afterEach(() => {
  cleanup()
})

// ------------------------------------------------------------------ //
//  3c — WS runtime validation                                          //
// ------------------------------------------------------------------ //

describe('3c: malformed WS payloads', () => {
  it('analysis-list-response missing request_id does not render "undefined"', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const msg = makeInbound('analysis-list-response', { ok: true, conversations: [] })
      renderBrowser({ messages: [msg] })
      expect(document.body.textContent).not.toContain('undefined')
    } finally {
      warnSpy.mockRestore()
    }
  })

  it('analysis-detail-response with wrong field types does not crash', () => {
    const msg = makeInbound('analysis-detail-response', {
      request_id: 'rid',
      ok: true,
      analysis: {
        conversation_id: 123,
        findings: 'not-an-array',
      },
    })
    renderBrowser({ messages: [msg] })
    expect(document.body.textContent).not.toContain('undefined')
  })

  it('malformed payload logs a console warning', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const msg = makeInbound('analysis-list-response', {
        conversations: 'not-an-array-either',
      })
      renderBrowser({ messages: [msg] })
      expect(warnSpy).toHaveBeenCalled()
    } finally {
      warnSpy.mockRestore()
    }
  })
})

// ------------------------------------------------------------------ //
//  3e — Debouncing / request deduplication                             //
// ------------------------------------------------------------------ //

describe('3e: request debouncing', () => {
  it('scan button cannot fire twice before first response arrives', async () => {
    const { sendMessage } = renderBrowser()
    const scanButton = await screen.findByRole('button', { name: /scan/i })

    // Rapid-fire: two synchronous clicks bypass the React-rendered
    // `disabled` guard. Dedup must live in the handler itself.
    await act(async () => {
      scanButton.click()
      scanButton.click()
    })

    const scanCalls = sendMessage.mock.calls.filter(
      (call) => call[0] === 'analysis-run-request',
    )
    expect(scanCalls.length).toBe(1)
  })
})

// ------------------------------------------------------------------ //
//  3i — Accessibility: dispute form labels + Escape-to-close           //
// ------------------------------------------------------------------ //

describe('3i: dispute form accessibility', () => {
  async function driveToDisputeForm() {
    const user = userEvent.setup()
    const utils = renderWithControlledMessages()
    const { sendMessage, pushInbound } = utils

    // 1. Mount fires fetchList — honour it with a list containing one entry.
    const listRid = await waitFor(() => lastRequestIdFor(sendMessage, 'analysis-list-request'))
    await act(async () => {
      pushInbound(makeInbound('analysis-list-response', {
        request_id: listRid,
        ok: true,
        conversations: [{
          date: '2026-04-22',
          conversation_id: 'c01',
          topic: 'Test conversation topic',
          analysed_at: '2026-04-22T10:00:00Z',
          requests_analysed: 1,
          total_steps: 3,
          avg_steps_per_request: 3,
          total_tool_calls: 2,
          git_ref: 'abc1234',
          finding_count: 1,
          severity_summary: { high: 0, medium: 1, low: 0 },
        }],
      }))
    })

    // 2. Click the conversation row — fires fetchDetail.
    const row = await screen.findByText(/test conversation topic/i)
    await user.click(row)

    // 3. Answer the detail request with one finding.
    const detailRid = await waitFor(() => lastRequestIdFor(sendMessage, 'analysis-detail-request'))
    await act(async () => {
      pushInbound(makeInbound('analysis-detail-response', {
        request_id: detailRid,
        ok: true,
        analysis: {
          analysed_at: '2026-04-22T10:00:00Z',
          git_ref: 'abc1234',
          conversation_id: 'c01',
          date: '2026-04-22',
          topic: 'Test conversation topic',
          requests_analysed: 1,
          total_tool_calls: 2,
          total_steps: 3,
          avg_steps_per_request: 3,
          notes: '',
          findings: [{
            request_id: 'rq-c01-0001',
            failure_mode: 'FM-11',
            failure_name: 'wrong zone',
            severity: 'medium',
            summary: 'Finding summary',
            detail: 'Finding detail',
          }],
        },
      }))
    })

    // feedback-status fetch fires after detail is applied; answer it
    // empty so the status-pending UI doesn't swallow the Dispute button.
    const feedbackRid = await waitFor(() => lastRequestIdFor(sendMessage, 'analysis-feedback-request'))
    await act(async () => {
      pushInbound(makeInbound('analysis-feedback-response', {
        request_id: feedbackRid,
        ok: true,
        items: [],
      }))
    })

    // 4. Expand the finding card (its header is a button).
    const findingHeader = await screen.findByText(/wrong zone/i)
    await user.click(findingHeader)

    // 5. Click Dispute to open the form.
    const disputeBtn = await screen.findByRole('button', { name: /^dispute$/i })
    await user.click(disputeBtn)

    return { user, ...utils }
  }

  it('dispute form inputs have associated labels', async () => {
    await driveToDisputeForm()
    expect(screen.getByLabelText(/disposition/i)).toBeTruthy()
    expect(screen.getByLabelText(/rebuttal/i)).toBeTruthy()
  })

  it('Escape key closes the open dispute form', async () => {
    const { user } = await driveToDisputeForm()

    // Form is open → we should see the Cancel button, not Dispute.
    expect(screen.queryByRole('button', { name: /cancel/i })).toBeTruthy()

    await user.keyboard('{Escape}')

    // Form closed → Dispute button is back.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^dispute$/i })).toBeTruthy()
    })
  })
})

// ------------------------------------------------------------------ //
//  Submitting a dispute refreshes the conversation list                //
//                                                                      //
//  Without this, the sidebar's pending-feedback dot only updates on    //
//  the next 30s list-poll tick, so the operator sees a ~30s gap        //
//  between submitting a dispute and the row showing it as pending.    //
// ------------------------------------------------------------------ //

describe('feedback submit refreshes list', () => {
  it('fires an analysis-list-request after a successful submit response', async () => {
    const user = userEvent.setup()
    const utils = renderWithControlledMessages()
    const { sendMessage, pushInbound } = utils

    // 1. Mount triggers a list fetch.
    const listRid = await waitFor(() => lastRequestIdFor(sendMessage, 'analysis-list-request'))
    await act(async () => {
      pushInbound(makeInbound('analysis-list-response', {
        request_id: listRid,
        ok: true,
        conversations: [{
          date: '2026-04-22',
          conversation_id: 'c01',
          topic: 'Test conversation topic',
          analysed_at: '2026-04-22T10:00:00Z',
          requests_analysed: 1,
          total_steps: 3,
          avg_steps_per_request: 3,
          total_tool_calls: 2,
          git_ref: 'abc1234',
          finding_count: 1,
          severity_summary: { high: 0, medium: 1, low: 0 },
        }],
      }))
    })

    // 2. Open the conversation, return one finding, no existing feedback.
    await user.click(await screen.findByText(/test conversation topic/i))
    const detailRid = await waitFor(() => lastRequestIdFor(sendMessage, 'analysis-detail-request'))
    await act(async () => {
      pushInbound(makeInbound('analysis-detail-response', {
        request_id: detailRid,
        ok: true,
        analysis: {
          analysed_at: '2026-04-22T10:00:00Z',
          git_ref: 'abc1234',
          conversation_id: 'c01',
          date: '2026-04-22',
          topic: 'Test conversation topic',
          requests_analysed: 1,
          total_tool_calls: 2,
          total_steps: 3,
          avg_steps_per_request: 3,
          notes: '',
          findings: [{
            request_id: 'rq-c01-0001',
            failure_mode: 'FM-11',
            failure_name: 'wrong zone',
            severity: 'medium',
            summary: 'Finding summary',
            detail: 'Finding detail',
          }],
        },
      }))
    })
    const fbStatusRid = await waitFor(() => lastRequestIdFor(sendMessage, 'analysis-feedback-request'))
    await act(async () => {
      pushInbound(makeInbound('analysis-feedback-response', {
        request_id: fbStatusRid, ok: true, items: [],
      }))
    })

    // 3. Open the dispute form, fill rebuttal, submit.
    await user.click(await screen.findByText(/wrong zone/i))
    await user.click(await screen.findByRole('button', { name: /^dispute$/i }))
    await user.type(screen.getByLabelText(/rebuttal/i), 'operator rebuttal')
    await user.click(screen.getByRole('button', { name: /submit/i }))

    // 4. Capture the submit-action request id and ack it server-side.
    const submitRid = await waitFor(() => {
      const calls = sendMessage.mock.calls.filter((c) => c[0] === 'analysis-feedback-request')
      const submitCall = calls.find((c) => JSON.parse(c[1] as string).action === 'submit')
      if (!submitCall) throw new Error('no submit call yet')
      return JSON.parse(submitCall[1] as string).request_id as string
    })

    // Snapshot list-request count BEFORE the response so the post-submit
    // refresh shows up as a delta — not just any list call from the
    // initial mount or polling.
    const listCallsBefore = sendMessage.mock.calls.filter(
      (c) => c[0] === 'analysis-list-request',
    ).length

    await act(async () => {
      pushInbound(makeInbound('analysis-feedback-response', {
        request_id: submitRid,
        ok: true,
        item: {
          request_id: 'rq-c01-0001',
          failure_mode: 'FM-11',
          disposition: 'dismiss',
          rebuttal: 'operator rebuttal',
          timestamp: '2026-04-22T10:01:00Z',
          lesson_status: 'pending',
          validation_iterations: 0,
        },
      }))
    })

    await waitFor(() => {
      const listCallsAfter = sendMessage.mock.calls.filter(
        (c) => c[0] === 'analysis-list-request',
      ).length
      expect(listCallsAfter).toBeGreaterThan(listCallsBefore)
    })
  })
})
