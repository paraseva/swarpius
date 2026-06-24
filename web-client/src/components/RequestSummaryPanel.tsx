import React from 'react'
import { RequestIdBadge } from './RequestIdBadge'
import { parseJson } from '../utils/parseJson'
import { useChannelHistory } from '../hooks/useChannelHistory'
import { scrollRequestIntoView } from '../hooks/useRequestFocusSync'
import { useRequestFocus } from '../requestFocusContext'
import { dayKey, dayLabel } from '../utils/dayLabel'
import s from './RequestSummaryPanel.module.css'

interface RequestCompleteEvent {
  event_type?: string
  request_id?: string
  total_steps?: number
  total_duration_ms?: number
  status?: string
  error?: string
  flags?: string[]
  conversation_id?: string
}

interface RequestStartEvent {
  event_type?: string
  source?: string
  request_id?: string
  text?: string
  user_input?: string
}

interface StepEvent {
  timestampMs: number
  label: string
  durationMs?: number
  promptTokens?: number
  outputTokens?: number
  cacheReadTokens?: number
  costUsd?: number
}

interface RequestSummary {
  requestId: string
  input: string
  steps: number
  durationMs: number
  status: string
  timestampMs: number
  conversationId: string
  coordinatorModel: string
  totalPromptTokens: number
  totalOutputTokens: number
  totalCacheReadTokens: number
  totalCostUsd: number
  events: StepEvent[]
  error?: string
}

interface ConversationGroup {
  groupKey: string
  conversationId: string
  requests: RequestSummary[]
  totalSteps: number
  totalDurationMs: number
  totalPromptTokens: number
  totalOutputTokens: number
  totalCacheReadTokens: number
  totalCostUsd: number
  latestTimestampMs: number
}

interface StepUsage {
  input_tokens?: number
  output_tokens?: number
  cache_read_input_tokens?: number
  cost_usd?: number
}

interface AgentOutputPayload {
  event_type?: string
  source?: string
  request_id?: string
  text?: string
  step?: number
  selected_skill?: string
  done?: boolean
  has_chat_response?: boolean
  duration_ms?: number
  total_steps?: number
  total_duration_ms?: number
  status?: string
  usage?: StepUsage
  coordinator_model?: string
  conversation_id?: string
}

const STATUS_LABELS: Record<string, string> = {
  completed: 'ok',
  problem: 'problem',
  error: 'error',
  interrupted: 'interrupted',
  max_steps_reached: 'max steps',
  awaiting_user_response: 'awaiting',
}

export const RequestSummaryPanel: React.FC = () => {
  const scrollContainerRef = React.useRef<HTMLDivElement | null>(null)
  // Load this panel's data directly (agent-outputs — where the request lifecycle
  // lives) via the same shared hook every panel uses, rather than deriving from
  // whatever other panels happen to have loaded.
  const agentMessages = useChannelHistory('agent-outputs', scrollContainerRef)
  const [expandedConversations, setExpandedConversations] = React.useState<Set<string>>(new Set())
  const [expandedRequests, setExpandedRequests] = React.useState<Set<string>>(new Set())

  const conversations = React.useMemo(() => {
    const requestInputs = new Map<string, string>()
    const requestEvents = new Map<string, StepEvent[]>()
    const requestTokens = new Map<string, { prompt: number; output: number; cacheRead: number; cost: number }>()
    const summaries: RequestSummary[] = []
    const seenRequestIds = new Set<string>()

    const ensureEvents = (rid: string) => {
      if (!requestEvents.has(rid)) requestEvents.set(rid, [])
    }
    const ensureTokens = (rid: string) => {
      if (!requestTokens.has(rid)) requestTokens.set(rid, { prompt: 0, output: 0, cacheRead: 0, cost: 0 })
    }

    for (const message of agentMessages) {
      const event = parseJson<RequestStartEvent & RequestCompleteEvent & AgentOutputPayload>(message.payload ?? message.body)
      if (!event || !event.request_id) continue
      const rid = event.request_id
      const ts = message.timestamp
      ensureEvents(rid)
      ensureTokens(rid)

      if (event.source === '[Request]' && event.user_input) {
        requestInputs.set(rid, event.user_input)
      }

      if (event.event_type === 'coordinator_step') {
        const skill = event.selected_skill ?? (event.has_chat_response ? 'chat_response' : 'none')
        const done = event.done ? ' (done)' : ''
        const u = event.usage
        const promptTokens = u?.input_tokens ?? 0
        const outputTokens = u?.output_tokens ?? 0
        const cacheReadTokens = u?.cache_read_input_tokens ?? 0
        const costUsd = u?.cost_usd ?? 0
        if (promptTokens > 0 || outputTokens > 0 || costUsd > 0) {
          const totals = requestTokens.get(rid)!
          totals.prompt += promptTokens
          totals.output += outputTokens
          totals.cacheRead += cacheReadTokens
          totals.cost += costUsd
        }
        requestEvents.get(rid)!.push({
          timestampMs: ts,
          label: `Step ${event.step}: ${skill}${done}`,
          durationMs: event.duration_ms,
          promptTokens: promptTokens || undefined,
          outputTokens: outputTokens || undefined,
          cacheReadTokens: cacheReadTokens || undefined,
          costUsd: costUsd || undefined,
        })
      }

      if (event.event_type === 'request_complete' && !seenRequestIds.has(rid)) {
        seenRequestIds.add(rid)
        const tokens = requestTokens.get(rid) ?? { prompt: 0, output: 0, cacheRead: 0, cost: 0 }
        summaries.push({
          requestId: rid,
          input: requestInputs.get(rid) ?? '',
          steps: event.total_steps ?? 0,
          durationMs: event.total_duration_ms ?? 0,
          status: event.status ?? 'unknown',
          timestampMs: ts,
          conversationId: event.conversation_id ?? '',
          coordinatorModel: event.coordinator_model ?? '',
          totalPromptTokens: tokens.prompt,
          totalOutputTokens: tokens.output,
          totalCacheReadTokens: tokens.cacheRead,
          totalCostUsd: tokens.cost,
          events: requestEvents.get(rid) ?? [],
          error: event.error,
        })
      }
    }

    const groupMap = new Map<string, ConversationGroup>()
    for (const req of summaries) {
      const cid = req.conversationId || '(none)'
      // Conversation ids reset daily, so the same cNN on two days is two
      // distinct conversations — key by id+day so they don't collapse together.
      const groupKey = `${cid}|${dayKey(req.timestampMs)}`
      let group = groupMap.get(groupKey)
      if (!group) {
        group = {
          groupKey,
          conversationId: cid,
          requests: [],
          totalSteps: 0,
          totalDurationMs: 0,
          totalPromptTokens: 0,
          totalOutputTokens: 0,
          totalCacheReadTokens: 0,
          totalCostUsd: 0,
          latestTimestampMs: 0,
        }
        groupMap.set(groupKey, group)
      }
      group.requests.push(req)
      group.totalSteps += req.steps
      group.totalDurationMs += req.durationMs
      group.totalPromptTokens += req.totalPromptTokens
      group.totalOutputTokens += req.totalOutputTokens
      group.totalCacheReadTokens += req.totalCacheReadTokens
      group.totalCostUsd += req.totalCostUsd
      if (req.timestampMs > group.latestTimestampMs) {
        group.latestTimestampMs = req.timestampMs
      }
    }

    // Sort conversations by latest request timestamp (most recent first)
    // Within each conversation, requests are in chronological order
    return Array.from(groupMap.values())
      .sort((a, b) => b.latestTimestampMs - a.latestTimestampMs)
      .slice(0, 20)
  }, [agentMessages])

  // Requests are grouped under collapsed conversations, so a focused request's
  // card may not be rendered yet. On focus, expand its conversation and mark it
  // pending; a second effect scrolls once the card has actually rendered (keyed
  // on expandedConversations, so it waits for the expand rather than guessing a
  // frame). conversationsRef keeps the lookup current without re-running on
  // every message.
  const focus = useRequestFocus()
  const focused = focus?.focusedRequest
  const pendingScrollRef = React.useRef<{ requestId: string; day: string | null } | null>(null)
  const conversationsRef = React.useRef(conversations)
  React.useEffect(() => {
    conversationsRef.current = conversations
  }, [conversations])

  React.useEffect(() => {
    if (!focused || focused.sourceKey === 'requests') return
    // Match the day too: conversation ids reset daily, so the same request id
    // can appear in more than one conversation group.
    const matches = (r: { requestId: string; timestampMs: number }) =>
      r.requestId === focused.requestId && (!focused.day || dayKey(r.timestampMs) === focused.day)
    const conv = conversationsRef.current.find((c) => c.requests.some(matches))
    if (!conv) return
    pendingScrollRef.current = { requestId: focused.requestId, day: focused.day }
    setExpandedConversations((prev) =>
      prev.has(conv.conversationId) ? prev : new Set(prev).add(conv.conversationId))
  }, [focused])

  React.useEffect(() => {
    const pending = pendingScrollRef.current
    if (pending
        && scrollRequestIntoView(scrollContainerRef.current, pending.requestId, pending.day)) {
      pendingScrollRef.current = null
    }
  }, [focused, expandedConversations])

  return (
    <div className="panel panel-history">
      <div className="panel-header">
        <h3>Session Requests</h3>
      </div>
      <div ref={scrollContainerRef} className="panel-body scrollable">
        <div data-history-top aria-hidden="true" />
        {conversations.length === 0 ? (
          <p className="empty-placeholder">No completed requests yet.</p>
        ) : (
          <ul className={s.list}>
            {conversations.map((conv) => {
              const isConvOpen = expandedConversations.has(conv.conversationId)
              return (
                <li key={conv.groupKey} className={s.conversationGroup}>
                  <button
                    type="button"
                    className={s.conversationHeader}
                    onClick={() => {
                      setExpandedConversations((prev) => {
                        const next = new Set(prev)
                        if (next.has(conv.conversationId)) next.delete(conv.conversationId)
                        else next.add(conv.conversationId)
                        return next
                      })
                    }}
                    aria-expanded={isConvOpen}
                  >
                    <span className={s.conversationId}>{conv.conversationId}</span>
                    <span className={s.conversationDate}>{dayLabel(conv.latestTimestampMs)}</span>
                    <span className={s.conversationMiddle}>
                      <span className={s.conversationStats}>
                        {conv.requests.length} req · {conv.totalSteps} steps · {(conv.totalDurationMs / 1000).toFixed(1)}s
                      </span>
                      <span className={s.requestTokens}>
                        {conv.totalPromptTokens > 0 ? (
                          <>
                            {(conv.totalPromptTokens - conv.totalCacheReadTokens).toLocaleString()} in / {conv.totalOutputTokens.toLocaleString()} out
                            {conv.totalCacheReadTokens > 0 ? (
                              <span className="token-cached"> (+{conv.totalCacheReadTokens.toLocaleString()} cached)</span>
                            ) : null}
                            {conv.totalCostUsd > 0 ? ` · $${conv.totalCostUsd.toFixed(3)}` : ''}
                          </>
                        ) : null}
                      </span>
                    </span>
                    <span className={s.statusDots} aria-hidden="true">
                      {conv.requests.map((req) => (
                        <span
                          key={req.requestId}
                          className={`${s.statusDot} ${req.status === 'completed' ? s.statusDotOk : s.statusDotError}`}
                          title={`${req.requestId}: ${STATUS_LABELS[req.status] ?? req.status}`}
                        />
                      ))}
                    </span>
                  </button>

                  {isConvOpen ? (
                    <ul className={s.conversationRequests}>
                      {conv.requests.map((req) => {
                        const isReqOpen = expandedRequests.has(req.requestId)
                        return (
                          <li
                            key={req.requestId}
                            data-request-id={req.requestId}
                            data-request-day={dayKey(req.timestampMs)}
                            className={`${s.requestItem} ${req.status !== 'completed' ? s.requestStatusError : ''}`}
                          >
                            <button
                              type="button"
                              className={s.requestHeader}
                              onClick={() => setExpandedRequests((prev) => {
                                const next = new Set(prev)
                                if (next.has(req.requestId)) next.delete(req.requestId)
                                else next.add(req.requestId)
                                return next
                              })}
                              aria-expanded={isReqOpen}
                              aria-label={req.requestId}
                            >
                              <span className={s.requestId}>
                                <RequestIdBadge requestId={req.requestId} syncKey="requests" day={dayKey(req.timestampMs)} />
                              </span>
                              <span className={s.requestTime}>
                                {new Date(req.timestampMs).toLocaleTimeString()}
                              </span>
                              {req.coordinatorModel && (
                                <span className={s.requestModel} title={req.coordinatorModel}>
                                  {req.coordinatorModel.replace(/^[^/]+\//, '')}
                                </span>
                              )}
                              <span className={s.requestInput} title={req.input || undefined}>
                                {req.input || '—'}
                              </span>
                              <span className={s.requestMiddle}>
                                <span className={s.requestSteps}>{req.steps} steps</span>
                                <span className={s.requestDuration}>{(req.durationMs / 1000).toFixed(1)}s</span>
                                <span className={s.requestTokens}>
                                  {req.totalPromptTokens > 0 ? (
                                    <>
                                      {(req.totalPromptTokens - req.totalCacheReadTokens).toLocaleString()} in / {req.totalOutputTokens.toLocaleString()} out
                                      {req.totalCacheReadTokens > 0 ? (
                                        <span className="token-cached"> (+{req.totalCacheReadTokens.toLocaleString()} cached)</span>
                                      ) : null}
                                      {req.totalCostUsd > 0 ? ` · $${req.totalCostUsd.toFixed(3)}` : ''}
                                    </>
                                  ) : null}
                                </span>
                              </span>
                              <span className={`${s.requestStatus} ${req.status === 'completed' ? s.requestStatusLabelOk : s.requestStatusLabelError}`}>
                                {STATUS_LABELS[req.status] ?? req.status}
                              </span>
                            </button>

                            {isReqOpen && (req.error || req.events.length > 0) ? (
                              <ul className={s.stepList}>
                                {req.error ? (
                                  <li className={`${s.stepItem} ${s.stepItemFailed}`} title={req.error}>
                                    <span className={`${s.stepIndicator} ${s.stepIndicatorError}`} />
                                    <span className={s.stepLabel}>{req.error}</span>
                                  </li>
                                ) : null}
                                {req.events.map((ev, idx) => (
                                  <li key={`step-${idx}`} className={s.stepItem}>
                                    <span className={`${s.stepIndicator} ${s.stepIndicatorOk}`} />
                                    <span className={s.stepLabel}>{ev.label}</span>
                                    {ev.durationMs != null ? (
                                      <span className={s.stepDuration}>{(ev.durationMs / 1000).toFixed(2)}s</span>
                                    ) : null}
                                    {ev.promptTokens != null ? (
                                      <span className={s.stepTokens}>
                                        {((ev.promptTokens ?? 0) - (ev.cacheReadTokens ?? 0)).toLocaleString()} in / {ev.outputTokens?.toLocaleString() ?? '0'} out
                                        {(ev.cacheReadTokens ?? 0) > 0 ? <span className="token-cached"> (+{ev.cacheReadTokens!.toLocaleString()} cached)</span> : ''}
                                        {(ev.costUsd ?? 0) > 0 ? ` · $${ev.costUsd!.toFixed(3)}` : ''}
                                      </span>
                                    ) : null}
                                  </li>
                                ))}
                              </ul>
                            ) : null}
                          </li>
                        )
                      })}
                    </ul>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
