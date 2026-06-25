import React from 'react'
import { useWebSocket } from '../websocketContext'
import { createUuid } from '../utils/uuid'
import { parseInboundPayload } from '../utils/parseJson'
import { PanelHeader } from './PanelHeader'
import { AnalysisDetailView } from './AnalysisDetailView'
import { AnalysisMetricsView } from './AnalysisMetricsView'
import {
  POLL_INTERVAL_MS,
  defaultDateFrom,
  defaultDateTo,
  feedbackKey,
  formatModelOption,
  type AnalysisDetail,
  type AnalysisListEntry,
  type AnalysisSubTab,
  type AnalysisUpdatePayload,
  type DetailResponse,
  type FeedbackItem,
  type FeedbackResponse,
  type ListResponse,
  type MetricsResponse,
  type RequestLogs,
  type RequestLogsResponse,
  type ResultHandleData,
  type ResultHandleResponse,
  type RunResponse,
} from './AnalysisBrowser.shared'
import { isDisplayableGitRef } from '../utils/gitRef'
import { useAutoDismiss } from '../hooks/useAutoDismiss'
import s from './AnalysisBrowser.module.css'

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const AnalysisBrowser: React.FC<{ onClose?: () => void }> = ({ onClose }) => {
  const { status, messages, sendMessage } = useWebSocket()

  const [subTab, setSubTab] = React.useState<AnalysisSubTab>('conversations')
  const [conversations, setConversations] = React.useState<AnalysisListEntry[]>([])
  const [selectedKey, setSelectedKey] = React.useState<string | null>(null)
  const [detail, setDetail] = React.useState<AnalysisDetail | null>(null)
  const [listLoading, setListLoading] = React.useState(false)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [scanLoading, setScanLoading] = React.useState(false)
  const [rerunLoading, setRerunLoading] = React.useState(false)
  const [scanResult, setScanResult] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  // The scan-result line is a one-shot status; clear it after a few
  // seconds so a stale "No new conversations" can't mislead later.
  const clearScanResult = React.useCallback(() => setScanResult(null), [])
  useAutoDismiss(Boolean(scanResult), clearScanResult)

  // Conversation list filters (default: last 3 days)
  const [dateFrom, setDateFrom] = React.useState(defaultDateFrom)
  const [dateTo, setDateTo] = React.useState(defaultDateTo)
  const [listModel, setListModel] = React.useState('')
  const [listModels, setListModels] = React.useState<string[]>([])

  // Metrics state
  const [metrics, setMetrics] = React.useState<MetricsResponse | null>(null)
  const [metricsLoading, setMetricsLoading] = React.useState(false)
  const [metricsAfter, setMetricsAfter] = React.useState('')
  const [metricsBefore, setMetricsBefore] = React.useState('')
  const [metricsRef, setMetricsRef] = React.useState('')
  const [metricsModel, setMetricsModel] = React.useState('')

  // Track pending request IDs so we only process our own responses
  const pendingListRef = React.useRef<string | null>(null)
  const pendingDetailRef = React.useRef<string | null>(null)
  const pendingRunRef = React.useRef<string | null>(null)
  const pendingMetricsRef = React.useRef<string | null>(null)

  // Request logs cache: rq_id → logs data (shared across findings)
  const [requestLogsCache, setRequestLogsCache] = React.useState<Record<string, RequestLogs>>({})
  const [requestLogsLoading, setRequestLogsLoading] = React.useState<Record<string, boolean>>({})
  const pendingLogsRef = React.useRef<Map<string, string>>(new Map())

  // Result handle cache: handle → data (shared across findings)
  const [resultHandleCache, setResultHandleCache] = React.useState<Record<string, ResultHandleData>>({})
  const [resultHandleLoading, setResultHandleLoading] = React.useState<Record<string, boolean>>({})
  const pendingResultHandleRef = React.useRef<Map<string, string>>(new Map())

  // Feedback: "<request_id>::<failure_mode>" → FeedbackItem (identity key)
  const [feedbackItems, setFeedbackItems] = React.useState<Record<string, FeedbackItem>>({})
  // Errors from submit/cancel actions, scoped to the identity that failed
  // so the relevant FindingCard can render the message inline rather than
  // dumping it into the global error banner (which sits next to the
  // conversation list and can easily be missed).
  const [feedbackErrors, setFeedbackErrors] = React.useState<Record<string, string>>({})
  const pendingFeedbackRef = React.useRef<Map<string, { action: string, identityKey?: string }>>(new Map())

  // ------- Send helpers -------

  const fetchList = React.useCallback(() => {
    const rid = createUuid()
    pendingListRef.current = rid
    setListLoading(true)
    const payload: Record<string, string> = { request_id: rid }
    if (dateFrom) payload.date_from = dateFrom
    if (dateTo) payload.date_to = dateTo
    if (listModel) payload.model = listModel
    sendMessage('analysis-list-request', JSON.stringify(payload))
  }, [sendMessage, dateFrom, dateTo, listModel])

  const fetchDetail = React.useCallback((date: string, conversationId: string) => {
    const rid = createUuid()
    pendingDetailRef.current = rid
    setDetailLoading(true)
    sendMessage('analysis-detail-request', JSON.stringify({
      request_id: rid,
      date,
      conversation_id: conversationId,
    }))
  }, [sendMessage])

  const triggerRerun = React.useCallback((date: string, conversationId: string) => {
    if (pendingRunRef.current) return
    const rid = createUuid()
    pendingRunRef.current = rid
    setRerunLoading(true)
    setError(null)
    sendMessage('analysis-run-request', JSON.stringify({
      request_id: rid,
      action: 'rerun',
      date,
      conversation_id: conversationId,
    }))
  }, [sendMessage])

  const triggerScan = React.useCallback(() => {
    if (pendingRunRef.current) return
    const rid = createUuid()
    pendingRunRef.current = rid
    setScanLoading(true)
    setScanResult(null)
    setError(null)
    sendMessage('analysis-run-request', JSON.stringify({
      request_id: rid,
      action: 'scan',
    }))
  }, [sendMessage])

  const fetchRequestLogs = React.useCallback((date: string, conversationId: string, rqId: string) => {
    if (requestLogsCache[rqId] || requestLogsLoading[rqId]) return
    const rid = createUuid()
    pendingLogsRef.current.set(rid, rqId)
    setRequestLogsLoading((prev) => ({ ...prev, [rqId]: true }))
    sendMessage('analysis-request-logs-request', JSON.stringify({
      request_id: rid,
      date,
      conversation_id: conversationId,
      rq_id: rqId,
    }))
  }, [sendMessage, requestLogsCache, requestLogsLoading])

  const fetchResultHandle = React.useCallback((date: string, conversationId: string, handle: string) => {
    if (resultHandleCache[handle] || resultHandleLoading[handle]) return
    const rid = createUuid()
    pendingResultHandleRef.current.set(rid, handle)
    setResultHandleLoading((prev) => ({ ...prev, [handle]: true }))
    sendMessage('analysis-result-handle-request', JSON.stringify({
      request_id: rid,
      date,
      conversation_id: conversationId,
      result_handle: handle,
    }))
  }, [sendMessage, resultHandleCache, resultHandleLoading])

  const submitFeedback = React.useCallback((
    date: string,
    conversationId: string,
    findingRequestId: string,
    failureMode: string,
    disposition: string,
    rebuttal: string,
  ) => {
    const rid = createUuid()
    const identityKey = `${findingRequestId}::${failureMode}`
    pendingFeedbackRef.current.set(rid, { action: 'submit', identityKey })
    // Clear any prior error for this finding — the operator is retrying.
    setFeedbackErrors((prev) => {
      if (!(identityKey in prev)) return prev
      const next = { ...prev }
      delete next[identityKey]
      return next
    })
    sendMessage('analysis-feedback-request', JSON.stringify({
      request_id: rid,
      action: 'submit',
      date,
      conversation_id: conversationId,
      finding_request_id: findingRequestId,
      failure_mode: failureMode,
      disposition,
      rebuttal,
    }))
  }, [sendMessage])

  const fetchFeedbackStatus = React.useCallback((date: string, conversationId: string) => {
    const rid = createUuid()
    pendingFeedbackRef.current.set(rid, { action: 'status' })
    sendMessage('analysis-feedback-request', JSON.stringify({
      request_id: rid,
      action: 'status',
      date,
      conversation_id: conversationId,
    }))
  }, [sendMessage])

  const cancelFeedback = React.useCallback((
    date: string,
    conversationId: string,
    findingRequestId: string,
    failureMode: string,
  ) => {
    const rid = createUuid()
    const identityKey = `${findingRequestId}::${failureMode}`
    pendingFeedbackRef.current.set(rid, { action: 'cancel', identityKey })
    sendMessage('analysis-feedback-request', JSON.stringify({
      request_id: rid,
      action: 'cancel',
      date,
      conversation_id: conversationId,
      finding_request_id: findingRequestId,
      failure_mode: failureMode,
    }))
  }, [sendMessage])

  const dismissFeedbackError = React.useCallback((identityKey: string) => {
    setFeedbackErrors((prev) => {
      if (!(identityKey in prev)) return prev
      const next = { ...prev }
      delete next[identityKey]
      return next
    })
  }, [])

  const fetchMetrics = React.useCallback(() => {
    const rid = createUuid()
    pendingMetricsRef.current = rid
    setMetricsLoading(true)
    const payload: Record<string, string> = { request_id: rid }
    if (metricsAfter) payload.after = metricsAfter
    if (metricsBefore) payload.before = metricsBefore
    if (metricsRef) payload.ref = metricsRef
    if (metricsModel) payload.model = metricsModel
    sendMessage('analysis-metrics-request', JSON.stringify(payload))
  }, [sendMessage, metricsAfter, metricsBefore, metricsRef, metricsModel])

  // ------- Process incoming messages -------

  const processedRef = React.useRef(0)

  React.useEffect(() => {
    // Only process new messages since last render
    const start = processedRef.current
    const newMessages = messages.slice(start)
    processedRef.current = messages.length

    for (const msg of newMessages) {
      if (msg.channel === 'analysis-list-response') {
        const payload = parseInboundPayload<ListResponse>(
          msg.payload ?? msg.body, 'analysis-list-response',
        )
        if (!payload || payload.request_id !== pendingListRef.current) continue
        pendingListRef.current = null
        setListLoading(false)
        if (payload.ok && payload.conversations) {
          setConversations(payload.conversations)
          if (payload.models) setListModels(payload.models)
        } else if (payload.error) {
          setError(payload.error)
        }
      }

      if (msg.channel === 'analysis-detail-response') {
        const payload = parseInboundPayload<DetailResponse>(
          msg.payload ?? msg.body, 'analysis-detail-response',
        )
        if (!payload || payload.request_id !== pendingDetailRef.current) continue
        pendingDetailRef.current = null
        setDetailLoading(false)
        if (payload.ok && payload.analysis) {
          setDetail(payload.analysis)
          setFeedbackItems({})
          fetchFeedbackStatus(payload.analysis.date, payload.analysis.conversation_id)
        } else if (payload.ok && !payload.analysis) {
          setDetail(null)
          setError('Analysis not found')
        } else if (payload.error) {
          setError(payload.error)
        }
      }

      if (msg.channel === 'analysis-run-response') {
        const payload = parseInboundPayload<RunResponse>(
          msg.payload ?? msg.body, 'analysis-run-response',
        )
        if (!payload || payload.request_id !== pendingRunRef.current) continue

        // The server acknowledges with {accepted: true} and keeps the
        // scan/rerun running on a background task. Don't clear the
        // loading state until the completion event arrives — or an
        // immediate error (neither accepted nor completed) comes back.
        if (payload.accepted && !payload.completed) {
          continue
        }

        pendingRunRef.current = null
        setScanLoading(false)
        setRerunLoading(false)

        if (payload.ok) {
          // Rerun: update detail in place and refresh feedback status.
          // Also kick off a list refresh so the per-row dot and xN
          // revision badge update in the same tick as the detail
          // panel — without this, list updates lag by one render +
          // list RTT (because the transition effect runs later).
          if (payload.analysis) {
            setDetail(payload.analysis)
            setFeedbackItems({})
            fetchFeedbackStatus(payload.analysis.date, payload.analysis.conversation_id)
            fetchList()
          }
          if (typeof payload.analysed_count === 'number') {
            const errCount = payload.errors?.length ?? 0
            const parts: string[] = []
            if (payload.analysed_count > 0) {
              parts.push(`${payload.analysed_count} conversation${payload.analysed_count !== 1 ? 's' : ''} analysed`)
            } else {
              parts.push('No new conversations to analyse')
            }
            if (errCount > 0) {
              parts.push(`${errCount} error${errCount !== 1 ? 's' : ''}`)
            }
            setScanResult(parts.join(', '))
            fetchList()
          }
        } else if (payload.error) {
          setError(payload.error)
        }
      }

      if (msg.channel === 'analysis-request-logs-response') {
        const payload = parseInboundPayload<RequestLogsResponse>(
          msg.payload ?? msg.body, 'analysis-request-logs-response',
        )
        if (!payload || !payload.request_id) continue
        const rqId = pendingLogsRef.current.get(payload.request_id)
        if (!rqId) continue
        pendingLogsRef.current.delete(payload.request_id)
        setRequestLogsLoading((prev) => ({ ...prev, [rqId]: false }))
        if (payload.ok && payload.logs) {
          setRequestLogsCache((prev) => ({ ...prev, [rqId]: payload.logs as RequestLogs }))
        }
      }

      if (msg.channel === 'analysis-result-handle-response') {
        const payload = parseInboundPayload<ResultHandleResponse>(
          msg.payload ?? msg.body, 'analysis-result-handle-response',
        )
        if (!payload || !payload.request_id) continue
        const handle = pendingResultHandleRef.current.get(payload.request_id)
        if (!handle) continue
        pendingResultHandleRef.current.delete(payload.request_id)
        setResultHandleLoading((prev) => ({ ...prev, [handle]: false }))
        if (payload.ok && payload.data) {
          setResultHandleCache((prev) => ({ ...prev, [handle]: payload.data as ResultHandleData }))
        }
      }

      if (msg.channel === 'analysis-feedback-response') {
        const payload = parseInboundPayload<FeedbackResponse>(
          msg.payload ?? msg.body, 'analysis-feedback-response',
        )
        if (!payload || !payload.request_id) continue
        const entry = pendingFeedbackRef.current.get(payload.request_id)
        if (!entry) continue
        pendingFeedbackRef.current.delete(payload.request_id)
        if (payload.ok) {
          if (entry.action === 'status' && payload.items) {
            const map: Record<string, FeedbackItem> = {}
            for (const item of payload.items) {
              map[feedbackKey(item)] = item
            }
            setFeedbackItems(map)
          } else if (entry.action === 'submit' && payload.item) {
            setFeedbackItems((prev) => ({ ...prev, [feedbackKey(payload.item!)]: payload.item! }))
            // Refresh the list so the pending-feedback dot on the
            // conversation row appears immediately rather than waiting
            // for the 30s list-poll tick.
            fetchList()
          } else if (entry.action === 'cancel' && detail) {
            // Re-fetch so local state matches the now-updated file.
            fetchFeedbackStatus(detail.date, detail.conversation_id)
            // Also refresh the list so the pending-feedback dot on the
            // conversation row clears promptly rather than waiting for
            // the 30s list-poll tick.
            fetchList()
          }
        } else if (payload.error) {
          if (entry.identityKey) {
            // Scope submit/cancel errors to the specific finding.
            setFeedbackErrors((prev) => ({ ...prev, [entry.identityKey!]: payload.error! }))
          } else {
            setError(payload.error)
          }
        }
      }

      if (msg.channel === 'analysis-metrics-response') {
        const payload = parseInboundPayload<MetricsResponse>(
          msg.payload ?? msg.body, 'analysis-metrics-response',
        )
        if (!payload || payload.request_id !== pendingMetricsRef.current) continue
        pendingMetricsRef.current = null
        setMetricsLoading(false)
        if (payload.ok) {
          setMetrics(payload)
        } else if (payload.error) {
          setError(payload.error)
        }
      }

      // Server-initiated push: list refresh, entry update, or feedback processing
      if (msg.channel === 'analysis-update') {
        const payload = parseInboundPayload<AnalysisUpdatePayload>(
          msg.payload ?? msg.body, 'analysis-update',
          { requireRequestId: false, requireType: true },
        )
        if (!payload) continue

        if (payload.type === 'list_refreshed' && payload.conversations) {
          setConversations(payload.conversations)
        }

        if (payload.type === 'list_entry_updated' && payload.entry) {
          setConversations((prev) => {
            const idx = prev.findIndex(
              (c) => c.date === payload.entry!.date && c.conversation_id === payload.entry!.conversation_id,
            )
            if (idx >= 0) {
              const next = [...prev]
              next[idx] = payload.entry!
              return next
            }
            // New entry — insert in sorted position (descending by date, conv_id)
            const next = [...prev, payload.entry!]
            next.sort((a, b) => (b.date + b.conversation_id).localeCompare(a.date + a.conversation_id))
            return next
          })
        }

      }
    }
  }, [messages, fetchList, detail, fetchFeedbackStatus])

  // ------- Fetch list on mount, filter change, + polling -------

  React.useEffect(() => {
    if (status !== 'open') return
    // Fetch list on mount and start a polling interval. The setState
    // inside fetchList is fine here — the data is coming from the
    // server (an external system), not derived from React state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchList()
    const interval = setInterval(fetchList, POLL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [fetchList, status])

  // Fast-poll the feedback status of the currently-open conversation
  // while any item is pending or processing — the UI relies on these
  // transitions to drive the dispute lock (e.g. when the scheduled
  // analyser picks up a pending item and flips it to processing).
  // Stops when nothing is active, so there's no background load in
  // the common case of an already-resolved conversation.
  const hasActiveFeedback = React.useMemo(
    () => Object.values(feedbackItems).some(
      (fb) => fb.lesson_status === 'pending' || fb.lesson_status === 'processing',
    ),
    [feedbackItems],
  )
  React.useEffect(() => {
    if (status !== 'open' || !detail || !hasActiveFeedback) return
    const interval = setInterval(() => {
      fetchFeedbackStatus(detail.date, detail.conversation_id)
    }, 2500)
    return () => clearInterval(interval)
  }, [status, detail, hasActiveFeedback, fetchFeedbackStatus])

  // When feedback transitions from active (pending/processing) to
  // resolved, the analyser has just re-analysed the conversation and
  // feedback.yaml has been cleared. The analysis detail in our state
  // is now stale — re-fetch so findings / notes reflect the new run.
  // Also clear per-finding feedback errors (they're tied to the old
  // state) and refresh the list so the pending-feedback dot clears.
  const prevActiveRef = React.useRef(false)
  React.useEffect(() => {
    if (prevActiveRef.current && !hasActiveFeedback && detail) {
      fetchDetail(detail.date, detail.conversation_id)
      setFeedbackErrors({})
      fetchList()
    }
    prevActiveRef.current = hasActiveFeedback
  }, [hasActiveFeedback, detail, fetchDetail, fetchList])

  // ------- Unmount cleanup -------

  // Pending-request-id refs accumulate entries for responses that never
  // arrive. During the component's lifetime this is bounded by actual
  // traffic, but on unmount we clear them explicitly so nothing lingers
  // if the component is later remounted in the same session.
  React.useEffect(() => {
    const pendingLogs = pendingLogsRef.current
    const pendingResultHandle = pendingResultHandleRef.current
    const pendingFeedback = pendingFeedbackRef.current
    return () => {
      pendingListRef.current = null
      pendingDetailRef.current = null
      pendingRunRef.current = null
      pendingMetricsRef.current = null
      pendingLogs.clear()
      pendingResultHandle.clear()
      pendingFeedback.clear()
    }
  }, [])

  // ------- Fetch metrics when tab is active or filters change -------

  const prevSubTabRef = React.useRef(subTab)
  React.useEffect(() => {
    if (subTab === 'metrics') {
      // Fetch when the metrics tab is opened. setState comes from
      // the network response, not derived from React state.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      fetchMetrics()
    }
    prevSubTabRef.current = subTab
  }, [subTab, fetchMetrics])

  // ------- Selection -------

  const handleSelect = React.useCallback((entry: AnalysisListEntry) => {
    const key = `${entry.date}/${entry.conversation_id}`
    setSelectedKey(key)
    setError(null)
    setRequestLogsCache({})
    setRequestLogsLoading({})
    setResultHandleCache({})
    setResultHandleLoading({})
    fetchDetail(entry.date, entry.conversation_id)
  }, [fetchDetail])

  // ------- Render -------

  return (
    <div className={s.analysisBrowser}>
      <PanelHeader
        title="Conversation Analysis"
        guidanceId="conversation-analysis"
        guidanceDevMode
        onClose={onClose}
        closeLabel="Close Conversation Analysis"
      />
      <div className={s.analysisSubTabBar}>
        <button
          type="button"
          className={`${s.analysisSubTab} ${subTab === 'conversations' ? s.active : ''}`}
          onClick={() => setSubTab('conversations')}
        >
          Conversations
        </button>
        <button
          type="button"
          className={`${s.analysisSubTab} ${subTab === 'metrics' ? s.active : ''}`}
          onClick={() => setSubTab('metrics')}
        >
          Metrics
        </button>
      </div>

      {subTab === 'conversations' && (
        <div className={s.analysisContent}>
          <div className={s.analysisListSection}>
            <div className={s.analysisListHeader}>
              <span className={s.analysisListTitle}>
                Conversations
                {conversations.length > 0 && (
                  <span className={s.analysisListCount}>{conversations.length}</span>
                )}
              </span>
              <button
                type="button"
                className={s.analysisScanButton}
                onClick={triggerScan}
                disabled={scanLoading}
                title="Scan for new conversations and analyse them"
              >
                {scanLoading ? 'Scanning...' : 'Scan & Analyse'}
              </button>
            </div>
            <div className={s.filterBar}>
              <label className={s.filterField}>
                <span>From</span>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                />
              </label>
              <label className={s.filterField}>
                <span>To</span>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                />
              </label>
              <label className={s.filterField}>
                <span>Model</span>
                <select
                  value={listModel}
                  onChange={(e) => setListModel(e.target.value)}
                  className={s.filterSelect}
                >
                  <option value="">All models</option>
                  {listModels.map((m) => (
                    <option key={m} value={m}>{formatModelOption(m, listModels)}</option>
                  ))}
                </select>
              </label>
            </div>
            {scanResult && (
              <div className={s.analysisScanResult}>{scanResult}</div>
            )}
            {error && (
              <div className={s.analysisError}>{error}</div>
            )}
            <div className={s.analysisConversationList}>
              {listLoading && conversations.length === 0 && (
                <div className={s.analysisEmpty}>Loading...</div>
              )}
              {!listLoading && conversations.length === 0 && (
                <div className={s.analysisEmpty}>No analyses available</div>
              )}
              {conversations.map((entry) => {
                const key = `${entry.date}/${entry.conversation_id}`
                const isSelected = key === selectedKey
                return (
                  <button
                    key={key}
                    type="button"
                    className={`${s.analysisConversationCard} ${isSelected ? s.selected : ''}`}
                    onClick={() => handleSelect(entry)}
                  >
                    <div className={s.analysisCardTop}>
                      <span className={s.analysisCardDate}>{entry.date}</span>
                      {entry.first_request_at && (
                        <span className={s.analysisCardTime}>{entry.first_request_at.slice(11, 16)}</span>
                      )}
                      <span className={s.analysisCardConvId}>{entry.conversation_id}</span>
                      <span className={s.analysisCardStats}>
                        {entry.requests_analysed}req / {entry.total_steps}steps
                        {(entry.total_cost_usd ?? 0) > 0 ? ` / $${entry.total_cost_usd!.toFixed(3)}` : ''}
                      </span>
                      {isDisplayableGitRef(entry.git_ref) && (
                        <span className={s.analysisCardRef}>{entry.git_ref.slice(0, 7)}</span>
                      )}
                      <span className={s.analysisCardRightGroup}>
                        {entry.coordinator_model && (
                          <span className={s.analysisCardModel} title={entry.coordinator_model}>
                            {entry.coordinator_model.replace(/^[^/]+\//, '')}
                          </span>
                        )}
                        <FindingBadge count={entry.finding_count} severity={entry.severity_summary} />
                      </span>
                    </div>
                    <div className={s.analysisCardBottom}>
                      <span className={s.analysisCardTopic} title={entry.topic}>
                        {entry.topic || 'Untitled'}
                      </span>
                      <span className={s.analysisCardIndicators}>
                        {(entry.analysis_revisions ?? 1) > 1 && (
                          <span className={s.analysisRevisionBadge} title={`Analysed ${entry.analysis_revisions} times`}>
                            &times;{entry.analysis_revisions}
                          </span>
                        )}
                        {(entry.feedback_count ?? 0) > 0 && (
                          <span
                            className={`${s.analysisFeedbackDot} ${(entry.pending_feedback ?? 0) > 0 ? s.pending : s.processed}`}
                            title={
                              (entry.pending_feedback ?? 0) > 0
                                ? `${entry.pending_feedback} pending feedback`
                                : `${entry.feedback_count} feedback (all processed)`
                            }
                          />
                        )}
                      </span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          <div className={s.analysisDetailSection}>
            {detailLoading && (
              <div className={s.analysisEmpty}>Loading analysis...</div>
            )}
            {!detailLoading && !detail && !selectedKey && (
              <div className={s.analysisEmpty}>Select a conversation to view analysis</div>
            )}
            {!detailLoading && !detail && selectedKey && !error && (
              <div className={s.analysisEmpty}>No analysis data available</div>
            )}
            {!detailLoading && detail && (
              <AnalysisDetailView
                detail={detail}
                onRerun={() => triggerRerun(detail.date, detail.conversation_id)}
                rerunLoading={rerunLoading}
                logsCache={requestLogsCache}
                logsLoading={requestLogsLoading}
                onFetchLogs={fetchRequestLogs}
                resultHandleCache={resultHandleCache}
                resultHandleLoading={resultHandleLoading}
                onFetchResultHandle={fetchResultHandle}
                feedbackItems={feedbackItems}
                onSubmitFeedback={(findingRequestId, failureMode, disp, reb) => submitFeedback(
                  detail.date, detail.conversation_id, findingRequestId, failureMode, disp, reb,
                )}
                onCancelFeedback={(findingRequestId, failureMode) => cancelFeedback(
                  detail.date, detail.conversation_id, findingRequestId, failureMode,
                )}
                feedbackErrors={feedbackErrors}
                onDismissFeedbackError={dismissFeedbackError}
                reanalysisInFlight={rerunLoading}
              />
            )}
          </div>
        </div>
      )}

      {subTab === 'metrics' && (
        <AnalysisMetricsView
          metrics={metrics}
          loading={metricsLoading}
          after={metricsAfter}
          before={metricsBefore}
          gitRef={metricsRef}
          model={metricsModel}
          onAfterChange={setMetricsAfter}
          onBeforeChange={setMetricsBefore}
          onRefChange={setMetricsRef}
          onModelChange={setMetricsModel}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const SEVERITY_BADGE_CLASS: Record<string, string> = {
  high: s.severityHigh,
  medium: s.severityMedium,
  low: s.severityLow,
}

const FindingBadge: React.FC<{ count: number; severity: Record<string, number> }> = ({ count, severity }) => {
  if (count === 0) {
    return (
      <span className={`${s.analysisFindingBadge} ${s.clean}`} title="No analysis findings">0</span>
    )
  }
  // Show the highest severity colour
  const highestSeverity = severity.high ? 'high' : severity.medium ? 'medium' : 'low'
  const breakdown = (['high', 'medium', 'low'] as const)
    .map((sev) => (severity[sev] ? `${severity[sev]} ${sev}` : null))
    .filter((s): s is string => s !== null)
    .join(', ')
  const tooltip = `${count} finding${count === 1 ? '' : 's'} — ${breakdown}`
  return (
    <span
      className={`${s.analysisFindingBadge} ${SEVERITY_BADGE_CLASS[highestSeverity] ?? ''}`}
      title={tooltip}
    >
      {count}
    </span>
  )
}
