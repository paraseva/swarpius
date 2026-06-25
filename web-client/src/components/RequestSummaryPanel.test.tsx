import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { WebSocketContext, type SocketMessage, type WebSocketContextValue } from '../websocketContext'
import { RequestSummaryPanel } from './RequestSummaryPanel'

let msgId = 0
const makeMsg = (
  channel: string,
  payload: Record<string, unknown>,
  direction: 'inbound' | 'outbound' = 'inbound',
  timestamp = Date.now(),
): SocketMessage => ({
  id: `m-${++msgId}`,
  channel,
  direction,
  body: JSON.stringify(payload),
  payload,
  timestamp,
})

const renderWithMessages = (messages: SocketMessage[]) => {
  const ctx: WebSocketContextValue = {
    status: 'open',
    messages,
    sendMessage: () => '',
    isLlmActive: false,
    latestZoneSnapshot: null,
    connectionGeneration: 0,
    isRestarting: false,
    markRestarting: () => {},
    trimmedCount: 0,
  }
  return render(
    <WebSocketContext.Provider value={ctx}>
      <RequestSummaryPanel />
    </WebSocketContext.Provider>,
  )
}

let tsCounter = 1711900000000

/**
 * Build a completed-request message set with realistic event ordering:
 *   request_start → (call_completed → coordinator_step)* → request_complete
 */
const buildRequest = (
  requestId: string,
  opts: {
    conversationId?: string
    status?: string
    error?: string
    steps?: number
    durationMs?: number
    inputText?: string
    llmCalls?: Array<{
      callId: string
      agentName: string
      promptTokens: number
      outputTokens: number
      cacheReadTokens?: number
      costUsd?: number
      durationMs: number
      skill?: string
      done?: boolean
    }>
  } = {},
): SocketMessage[] => {
  let ts = tsCounter
  tsCounter += 10000 // separate requests in time
  const msgs: SocketMessage[] = []

  msgs.push(
    makeMsg('agent-outputs', {
      source: '[Request]',
      request_id: requestId,
      user_input: opts.inputText,
    }, 'inbound', ts),
  )

  const calls = opts.llmCalls ?? []
  for (let i = 0; i < calls.length; i++) {
    const call = calls[i]
    ts += 10
    msgs.push(
      makeMsg('agent-outputs', {
        event_type: 'coordinator_step',
        request_id: requestId,
        step: i + 1,
        selected_skill: call.skill ?? 'roon_action',
        done: call.done ?? (i === calls.length - 1),
        duration_ms: call.durationMs + 50,
        usage: {
          input_tokens: call.promptTokens,
          output_tokens: call.outputTokens,
          cache_read_input_tokens: call.cacheReadTokens ?? 0,
          cost_usd: call.costUsd ?? 0,
        },
      }, 'inbound', ts),
    )
  }

  ts += 50
  msgs.push(
    makeMsg('agent-outputs', {
      event_type: 'request_complete',
      request_id: requestId,
      total_steps: opts.steps ?? (calls.length || 2),
      total_duration_ms: opts.durationMs ?? 3500,
      status: opts.status ?? 'completed',
      error: opts.error,
      conversation_id: opts.conversationId ?? 'c01',
    }, 'inbound', ts),
  )

  return msgs
}

/** Get all conversation-level <li> elements (direct children of the top-level list). */
const getConversationGroups = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>('ul > li'))
    .filter((li) => li.parentElement?.parentElement?.classList.contains('panel-body'))

/** Get the conversation-level toggle button inside a group. */
const getConversationButton = (group: HTMLElement) =>
  group.querySelector<HTMLButtonElement>('button[aria-expanded]')!

/** Get all request-level toggle buttons (inside expanded conversation). */
const getRequestButtons = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLButtonElement>('button[aria-label]'))

describe('RequestSummaryPanel', () => {
  afterEach(() => {
    cleanup()
    tsCounter = 1711900000000
  })

  // ── Level 1: Conversations ──────────────────────────────────────

  it('renders empty state when no requests', () => {
    renderWithMessages([])
    expect(screen.getByText('No completed requests yet.')).toBeInTheDocument()
  })

  it('groups requests by conversation ID', () => {
    const msgs = [
      ...buildRequest('rq-c01-0001', { conversationId: 'c01', inputText: 'First' }),
      ...buildRequest('rq-c01-0002', { conversationId: 'c01', inputText: 'Second' }),
      ...buildRequest('rq-c02-0001', { conversationId: 'c02', inputText: 'Third' }),
    ]

    const { container } = renderWithMessages(msgs)

    const groups = getConversationGroups(container)
    expect(groups.length).toBe(2)
    // Most recent conversation first
    expect(within(groups[0]).getByText('c02')).toBeInTheDocument()
    expect(within(groups[1]).getByText('c01')).toBeInTheDocument()
  })

  it('keeps same request id on different days as two distinct requests', () => {
    // cNN / request ids reset each day, so rq-c01-0001 on two days is two
    // distinct requests — the later must not be deduped away by the earlier.
    const DAY = 24 * 60 * 60 * 1000
    tsCounter = 1711900000000
    const earlier = buildRequest('rq-c01-0001', { conversationId: 'c01', inputText: 'Monday ask' })
    tsCounter = 1711900000000 + 2 * DAY
    const later = buildRequest('rq-c01-0001', { conversationId: 'c01', inputText: 'Wednesday ask' })

    const { container } = renderWithMessages([...earlier, ...later])

    // Two day-groups, both present (the later request isn't dropped).
    expect(getConversationGroups(container).length).toBe(2)
  })

  it('aggregates token totals at conversation level', () => {
    const msgs = [
      ...buildRequest('rq-c01-0001', {
        conversationId: 'c01',
        llmCalls: [
          { callId: 'c1', agentName: 'Coordinator', promptTokens: 1000, outputTokens: 200, cacheReadTokens: 500, durationMs: 800 },
        ],
      }),
      ...buildRequest('rq-c01-0002', {
        conversationId: 'c01',
        llmCalls: [
          { callId: 'c2', agentName: 'Coordinator', promptTokens: 1500, outputTokens: 300, cacheReadTokens: 700, durationMs: 900 },
        ],
      }),
    ]

    const { container } = renderWithMessages(msgs)

    const groups = getConversationGroups(container)
    const header = getConversationButton(groups[0])
    // Aggregate: 1,300 in / 500 out (+1,200 cached)  (net new = 2,500 - 1,200)
    expect(within(header).getByText(/1,300/)).toBeInTheDocument()
    expect(within(header).getByText(/500 out/)).toBeInTheDocument()
    expect(within(header).getByText(/1,200/)).toBeInTheDocument()
  })

  it('shows status dots for each request in conversation', () => {
    const msgs = [
      ...buildRequest('rq-c01-0001', { conversationId: 'c01', status: 'completed' }),
      ...buildRequest('rq-c01-0002', { conversationId: 'c01', status: 'error' }),
      ...buildRequest('rq-c01-0003', { conversationId: 'c01', status: 'completed' }),
    ]

    const { container } = renderWithMessages(msgs)

    // Status dots have title attributes with request ID and status
    const dots = container.querySelectorAll<HTMLElement>('span[title^="rq-"]')
    expect(dots.length).toBe(3)
    const okDots = Array.from(dots).filter((d) => d.title.includes('ok'))
    const errorDots = Array.from(dots).filter((d) => d.title.includes('error'))
    expect(okDots.length).toBe(2)
    expect(errorDots.length).toBe(1)
  })

  // ── Level 2: Requests ──────────────────────────────────────────

  it('expands conversation to show request cards', async () => {
    const user = userEvent.setup()
    const msgs = [
      ...buildRequest('rq-c01-0001', { conversationId: 'c01', inputText: 'Play jazz' }),
      ...buildRequest('rq-c01-0002', { conversationId: 'c01', inputText: 'Skip track' }),
    ]

    renderWithMessages(msgs)

    // Requests not visible before expand
    expect(screen.queryByText('Play jazz')).toBeNull()

    const header = screen.getByRole('button', { expanded: false })
    await user.click(header)

    // Requests visible after expand
    expect(screen.getByText('Play jazz')).toBeInTheDocument()
    expect(screen.getByText('Skip track')).toBeInTheDocument()
  })

  it('request cards do not show conversation ID column', async () => {
    const user = userEvent.setup()
    const msgs = buildRequest('rq-c01-0001', { conversationId: 'c01', inputText: 'Hello' })

    const { container } = renderWithMessages(msgs)

    const groups = getConversationGroups(container)
    await user.click(getConversationButton(groups[0]))

    // cXX shown at conversation level, not repeated in request row
    const reqButtons = getRequestButtons(container)
    expect(reqButtons.length).toBe(1)
    expect(within(reqButtons[0]).queryByText('c01')).toBeNull()
  })

  it('shows per-request token totals and status', async () => {
    const user = userEvent.setup()
    const msgs = buildRequest('rq-c01-0001', {
      conversationId: 'c01',
      inputText: 'Search',
      durationMs: 3000,
      llmCalls: [
        { callId: 'c1', agentName: 'Coordinator', promptTokens: 800, outputTokens: 150, cacheReadTokens: 400, durationMs: 700 },
      ],
    })

    const { container } = renderWithMessages(msgs)

    const groups = getConversationGroups(container)
    await user.click(getConversationButton(groups[0]))

    const reqButton = getRequestButtons(container)[0]
    // Net new: 800 - 400 = 400 in / 150 out (+400 cached)
    expect(within(reqButton).getByText(/400 in/)).toBeInTheDocument()
    expect(within(reqButton).getByText(/150 out/)).toBeInTheDocument()
    expect(within(reqButton).getByText('ok')).toBeInTheDocument()
  })

  // ── Level 3: Steps ─────────────────────────────────────────────

  it('expands request to show step list with tokens', async () => {
    const user = userEvent.setup()
    const msgs = buildRequest('rq-c01-0001', {
      conversationId: 'c01',
      llmCalls: [
        { callId: 'c1', agentName: 'Coordinator', promptTokens: 1000, outputTokens: 200, cacheReadTokens: 500, durationMs: 1200, skill: 'roon_search' },
        { callId: 'c2', agentName: 'Coordinator', promptTokens: 1100, outputTokens: 180, durationMs: 900, skill: 'chat_response', done: true },
      ],
    })

    const { container } = renderWithMessages(msgs)

    // Expand conversation
    const groups = getConversationGroups(container)
    await user.click(getConversationButton(groups[0]))
    // Expand request
    const reqButton = getRequestButtons(container)[0]
    await user.click(reqButton)

    expect(screen.getByText('Step 1: roon_search')).toBeInTheDocument()
    expect(screen.getByText('Step 2: chat_response (done)')).toBeInTheDocument()
    // Step 1: net new = 1,000 - 500 = 500
    expect(screen.getByText(/500 in \/ 200 out/)).toBeInTheDocument()
  })

  it('does not show tool I/O events in step timeline', async () => {
    const user = userEvent.setup()
    const msgs = [
      ...buildRequest('rq-c01-0001', {
        conversationId: 'c01',
        llmCalls: [
          { callId: 'c1', agentName: 'Coordinator', promptTokens: 800, outputTokens: 150, durationMs: 700, skill: 'roon_search' },
        ],
      }),
      makeMsg('tool-outputs', { label: 'roon_search input', request_id: 'rq-c01-0001' }, 'inbound', tsCounter - 5000),
      makeMsg('tool-outputs', { label: 'roon_search output', request_id: 'rq-c01-0001' }, 'inbound', tsCounter - 4900),
    ]

    const { container } = renderWithMessages(msgs)

    const groups = getConversationGroups(container)
    await user.click(getConversationButton(groups[0]))
    const reqButton = getRequestButtons(container)[0]
    await user.click(reqButton)

    // Steps visible but tool I/O lines are not
    expect(screen.getByText(/Step 1: roon_search/)).toBeInTheDocument()
    expect(screen.queryByText('roon_search input')).toBeNull()
    expect(screen.queryByText('roon_search output')).toBeNull()
  })

  it('surfaces a failed request with its reason in the timeline', async () => {
    const user = userEvent.setup()
    const msgs = buildRequest('rq-c01-0001', {
      conversationId: 'c01',
      inputText: 'Play jazz',
      status: 'error',
      error: 'Timeout: connection timed out',
    })

    const { container } = renderWithMessages(msgs)

    // A failed request still appears (its lifecycle is closed with status=error).
    const groups = getConversationGroups(container)
    await user.click(getConversationButton(groups[0]))
    const reqButton = getRequestButtons(container)[0]
    expect(within(reqButton).getByText('error')).toBeInTheDocument()

    // Expanding it shows the failure reason.
    await user.click(reqButton)
    expect(screen.getByText(/Timeout: connection timed out/)).toBeInTheDocument()
  })

  // ── Collapse behaviour ─────────────────────────────────────────

  it('collapses conversation on second click', async () => {
    const user = userEvent.setup()
    const msgs = buildRequest('rq-c01-0001', { conversationId: 'c01', inputText: 'Hello' })

    renderWithMessages(msgs)

    const header = screen.getByRole('button', { expanded: false })
    await user.click(header)
    expect(screen.getByText('Hello')).toBeInTheDocument()

    await user.click(header)
    expect(screen.queryByText('Hello')).toBeNull()
  })

  it('conversations ordered most recent first', () => {
    const msgs = [
      ...buildRequest('rq-c01-0001', { conversationId: 'c01' }),
      ...buildRequest('rq-c02-0001', { conversationId: 'c02' }),
      ...buildRequest('rq-c01-0002', { conversationId: 'c01' }),
    ]

    const { container } = renderWithMessages(msgs)

    const groups = getConversationGroups(container)
    // c01 has the latest request (rq-c01-0002), so it should be first
    expect(within(groups[0]).getByText('c01')).toBeInTheDocument()
    expect(within(groups[1]).getByText('c02')).toBeInTheDocument()
  })

  it('shows per-request cost rounded to 3 decimal places', async () => {
    const user = userEvent.setup()
    const msgs = buildRequest('rq-c09-0001', {
      conversationId: 'c09',
      llmCalls: [
        { callId: 'c1', agentName: 'Coordinator', promptTokens: 1000, outputTokens: 200, costUsd: 0.00456, durationMs: 300 },
        { callId: 'c2', agentName: 'Coordinator', promptTokens: 500, outputTokens: 100, costUsd: 0.00123, durationMs: 200 },
      ],
    })
    const { container } = renderWithMessages(msgs)
    // Expand the conversation so the request card is visible
    await user.click(getConversationButton(getConversationGroups(container)[0]))

    // 0.00456 + 0.00123 = 0.00579, rounded to 3dp → $0.006
    // Appears in both the conversation header and the request card
    expect(screen.getAllByText(/\$0\.006/).length).toBeGreaterThan(0)
  })

  it('shows per-step cost when a step has non-zero cost', async () => {
    const user = userEvent.setup()
    const msgs = buildRequest('rq-c10-0001', {
      conversationId: 'c10',
      llmCalls: [
        { callId: 'c1', agentName: 'Coordinator', promptTokens: 1000, outputTokens: 200, costUsd: 0.00789, durationMs: 300 },
      ],
    })
    const { container } = renderWithMessages(msgs)
    await user.click(getConversationButton(getConversationGroups(container)[0]))
    const reqButton = getRequestButtons(container)[0]
    await user.click(reqButton)

    // Step-level cost row should be visible — 0.00789 rounds to $0.008
    // Multiple $0.008 entries may appear (request total + step) — use queryAllByText
    expect(screen.getAllByText(/\$0\.008/).length).toBeGreaterThan(0)
  })

  it('aggregates per-conversation cost across multiple requests', () => {
    const msgs: SocketMessage[] = [
      ...buildRequest('rq-c11-0001', {
        conversationId: 'c11',
        llmCalls: [
          { callId: 'c1', agentName: 'Coordinator', promptTokens: 500, outputTokens: 100, costUsd: 0.005, durationMs: 100 },
        ],
      }),
      ...buildRequest('rq-c11-0002', {
        conversationId: 'c11',
        llmCalls: [
          { callId: 'c2', agentName: 'Coordinator', promptTokens: 500, outputTokens: 100, costUsd: 0.012, durationMs: 100 },
        ],
      }),
    ]
    renderWithMessages(msgs)

    // Conversation total: 0.005 + 0.012 = 0.017 → $0.017
    expect(screen.getByText(/\$0\.017/)).toBeInTheDocument()
  })

  it('omits cost display when all calls have zero cost', () => {
    const msgs = buildRequest('rq-c12-0001', {
      conversationId: 'c12',
      llmCalls: [
        { callId: 'c1', agentName: 'Coordinator', promptTokens: 1000, outputTokens: 200, durationMs: 200 },
      ],
    })
    renderWithMessages(msgs)
    // No $X.XXX patterns should appear anywhere
    expect(screen.queryByText(/\$\d+\.\d{3}/)).not.toBeInTheDocument()
  })
})
