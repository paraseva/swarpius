import React from 'react'
import { RequestIdBadge } from './RequestIdBadge'
import { AnalysisHistoryView } from './AnalysisHistoryView'
import { FindingCard } from './FindingCard'
import { LinkedText } from './LinkedText'
import { RequestLogsView } from './RequestLogsView'
import { RevokedFindingsSection } from './RevokedFindingsSection'
import {
  feedbackKey,
  formatTokenCount,
  type AnalysisDetail,
  type FeedbackItem,
  type RequestLogs,
  type RequestSummary,
  type ResultHandleData,
} from './AnalysisBrowser.shared'
import { isDisplayableGitRef } from '../utils/gitRef'
import s from './AnalysisBrowser.module.css'

export const AnalysisDetailView: React.FC<{
  detail: AnalysisDetail
  onRerun: () => void
  rerunLoading: boolean
  logsCache: Record<string, RequestLogs>
  logsLoading: Record<string, boolean>
  onFetchLogs: (date: string, conversationId: string, rqId: string) => void
  resultHandleCache: Record<string, ResultHandleData>
  resultHandleLoading: Record<string, boolean>
  onFetchResultHandle: (date: string, conversationId: string, handle: string) => void
  feedbackItems: Record<string, FeedbackItem>
  feedbackErrors: Record<string, string>
  onSubmitFeedback: (findingRequestId: string, failureMode: string, disposition: string, rebuttal: string) => void
  onCancelFeedback: (findingRequestId: string, failureMode: string) => void
  onDismissFeedbackError: (identityKey: string) => void
  // True between the operator clicking Re-Analyse and the response
  // coming back. Polling at 2.5s can miss short processing windows,
  // and while the operator's click is in flight we already know a
  // re-analysis is starting — optimistically lock pending items here
  // so the form can't be cancelled mid-run.
  reanalysisInFlight: boolean
}> = ({ detail, onRerun, rerunLoading, logsCache, logsLoading, onFetchLogs, resultHandleCache, resultHandleLoading, onFetchResultHandle, feedbackItems, feedbackErrors, onSubmitFeedback, onCancelFeedback, onDismissFeedbackError, reanalysisInFlight }) => {
  // Bind date/conversationId so child components get simpler callbacks
  const boundFetchLogs = React.useCallback(
    (rqId: string) => onFetchLogs(detail.date, detail.conversation_id, rqId),
    [onFetchLogs, detail.date, detail.conversation_id],
  )
  const boundFetchResultHandle = React.useCallback(
    (handle: string) => onFetchResultHandle(detail.date, detail.conversation_id, handle),
    [onFetchResultHandle, detail.date, detail.conversation_id],
  )

  // Request rows: expand/collapse + scroll-to-request from findings
  const [showRequests, setShowRequests] = React.useState(false)
  const [expandedRequests, setExpandedRequests] = React.useState<Set<string>>(new Set())
  const flashTargetRef = React.useRef<string | null>(null)
  const [flashTick, setFlashTick] = React.useState(0)
  const requestRowRefs = React.useRef<Map<string, HTMLDivElement>>(new Map())

  // Reverse direction: clicking a request row's "finding" badge scrolls
  // to + flashes every finding card whose request_id matches. Multiple
  // findings can share a request_id, so refs are keyed by their index
  // in detail.findings (stable for a given analysis).
  const findingCardRefs = React.useRef<(HTMLDivElement | null)[]>([])
  const flashFindingsTargetRef = React.useRef<string | null>(null)
  const [flashFindingsTick, setFlashFindingsTick] = React.useState(0)

  // Flash the target request row after React has committed DOM updates.
  // Uses a tick counter so repeated clicks on the same request always re-flash.
  React.useEffect(() => {
    const rqId = flashTargetRef.current
    if (!rqId) return
    flashTargetRef.current = null
    const el = requestRowRefs.current.get(rqId)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    // Remove class and re-add in a separate frame to guarantee animation restart
    el.classList.remove('linked-ref-flash')
    requestAnimationFrame(() => {
      el.classList.add('linked-ref-flash')
    })
  }, [flashTick])

  // Flash every finding card matching the target request_id. Same
  // pattern as flashTargetRef above, but a single click can flash N
  // cards rather than one.
  React.useEffect(() => {
    const rqId = flashFindingsTargetRef.current
    if (!rqId) return
    flashFindingsTargetRef.current = null
    const elements = detail.findings
      .map((f, i) => f.request_id === rqId ? findingCardRefs.current[i] : null)
      .filter((el): el is HTMLDivElement => el != null)
    if (elements.length === 0) return
    elements[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    for (const el of elements) {
      el.classList.remove('linked-ref-flash')
    }
    requestAnimationFrame(() => {
      for (const el of elements) {
        el.classList.add('linked-ref-flash')
      }
    })
  }, [flashFindingsTick, detail.findings])

  const findingRequestIds = React.useMemo(
    () => new Set(detail.findings.map((f) => f.request_id)),
    [detail.findings],
  )

  const toggleRequest = React.useCallback((rqId: string) => {
    setExpandedRequests((prev) => {
      const next = new Set(prev)
      if (next.has(rqId)) { next.delete(rqId) } else { next.add(rqId) }
      return next
    })
  }, [])

  const scrollToRequest = React.useCallback((rqId: string) => {
    setShowRequests(true)
    setExpandedRequests((prev) => {
      const next = new Set(prev)
      next.add(rqId)
      return next
    })
    if (!logsCache[rqId] && !logsLoading[rqId]) {
      boundFetchLogs(rqId)
    }
    flashTargetRef.current = rqId
    setFlashTick((t) => t + 1)
  }, [boundFetchLogs, logsCache, logsLoading])

  const scrollToFinding = React.useCallback((rqId: string) => {
    flashFindingsTargetRef.current = rqId
    setFlashFindingsTick((t) => t + 1)
  }, [])

  const linkedTextProps = {
    resultHandleCache, resultHandleLoading, onFetchResultHandle: boundFetchResultHandle,
    onScrollToRequest: scrollToRequest,
  }

  return (
    <div className={s.analysisDetail}>
      <div className={s.analysisDetailHeader}>
        <div className={s.analysisDetailTitle}>
          <span className={s.analysisDetailConvId}>{detail.conversation_id}</span>
          <span className={s.analysisDetailDate}>{detail.date}</span>
          {detail.requests?.[0]?.timestamp && (
            <span className={s.analysisDetailTime}>{detail.requests[0].timestamp.slice(11, 16)}</span>
          )}
          {isDisplayableGitRef(detail.git_ref) && (
            <span className={s.analysisDetailRef} title="Git ref">{detail.git_ref.slice(0, 7)}</span>
          )}
        </div>
        <button
          type="button"
          className={s.analysisRerunButton}
          onClick={onRerun}
          disabled={rerunLoading}
          title="Re-analyse this conversation"
        >
          {rerunLoading ? 'Analysing...' : 'Re-analyse'}
        </button>
      </div>

      <div className={s.analysisDetailTopic}>{detail.topic}</div>

      <div className={s.analysisDetailStats}>
        <span>{detail.requests_analysed} requests</span>
        <span>{detail.total_steps} steps</span>
        <span>{detail.avg_steps_per_request.toFixed(1)} avg</span>
        <span>{detail.total_tool_calls} tool calls</span>
        {(() => {
          const totalInput = (detail.requests ?? []).reduce(
            (sum: number, rq: RequestSummary) => sum + (rq.usage?.input_tokens ?? 0), 0,
          )
          const totalCacheRead = (detail.requests ?? []).reduce(
            (sum: number, rq: RequestSummary) => sum + (rq.usage?.cache_read_input_tokens ?? 0), 0,
          )
          const netInput = totalInput - totalCacheRead
          return netInput > 0 ? <span>{formatTokenCount(netInput)} net tokens</span> : null
        })()}
        {(() => {
          const totalCost = (detail.requests ?? []).reduce(
            (sum: number, rq: RequestSummary) => sum + (rq.usage?.cost_usd ?? 0), 0,
          )
          return totalCost > 0 ? <span>${totalCost.toFixed(3)}</span> : null
        })()}
      </div>

      {detail.requests && detail.requests.length > 0 && (
        <div className={s.analysisRequests}>
          <button
            type="button"
            className={s.analysisSectionToggle}
            onClick={() => setShowRequests(!showRequests)}
          >
            <span className={s.analysisFindingsTitle}>Requests ({detail.requests.length})</span>
            <svg
              className={`${s.analysisFindingChevron} ${showRequests ? s.expanded : ''}`}
              viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round"
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
          {showRequests && detail.requests.map((rq) => {
            const isExpanded = expandedRequests.has(rq.request_id)
            const hasFinding = findingRequestIds.has(rq.request_id)
            return (
              <div
                key={rq.request_id}
                className={s.analysisRequestRow}
                ref={(el) => { if (el) requestRowRefs.current.set(rq.request_id, el) }}
              >
                <button
                  type="button"
                  className={s.analysisRequestHeader}
                  onClick={() => {
                    toggleRequest(rq.request_id)
                    if (!isExpanded && !logsCache[rq.request_id] && !logsLoading[rq.request_id]) {
                      boundFetchLogs(rq.request_id)
                    }
                  }}
                >
                  <RequestIdBadge requestId={rq.request_id} />
                  {rq.timestamp && (
                    <span className={s.analysisRequestTime}>
                      {rq.timestamp.slice(11, 16)}
                    </span>
                  )}
                  <span className={s.analysisRequestInput}>
                    {rq.user_input
                      ? (rq.user_input.length > 60 ? rq.user_input.slice(0, 60) + '...' : rq.user_input)
                      : '—'}
                  </span>
                  <span className={s.analysisRequestMeta}>
                    {rq.coordinator_model && (
                      <>{rq.coordinator_model.replace(/^[^/]+\//, '')} &middot; </>
                    )}
                    {rq.status ?? '?'} &middot; {rq.total_steps ?? '?'} steps
                    {rq.total_duration_ms != null ? ` · ${rq.total_duration_ms}ms` : ''}
                    {rq.usage?.input_tokens != null ? ` · ${formatTokenCount((rq.usage.input_tokens ?? 0) - (rq.usage.cache_read_input_tokens ?? 0))} net` : ''}
                    {(rq.usage?.cost_usd ?? 0) > 0 ? ` · $${(rq.usage!.cost_usd!).toFixed(3)}` : ''}
                  </span>
                  {hasFinding && (
                    <span
                      className={s.analysisRequestFindingBadge}
                      role="button"
                      tabIndex={0}
                      title="Jump to this request's finding(s)"
                      onClick={(e) => {
                        e.stopPropagation()
                        scrollToFinding(rq.request_id)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          e.stopPropagation()
                          scrollToFinding(rq.request_id)
                        }
                      }}
                    >
                      finding
                    </span>
                  )}
                  <svg
                    className={`${s.analysisFindingChevron} ${isExpanded ? s.expanded : ''}`}
                    viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                    strokeLinecap="round" strokeLinejoin="round"
                  >
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </button>
                {isExpanded && logsLoading[rq.request_id] && (
                  <div className={s.analysisRequestLoading}>Loading...</div>
                )}
                {isExpanded && logsCache[rq.request_id] && (
                  <RequestLogsView logs={logsCache[rq.request_id]} />
                )}
              </div>
            )
          })}
        </div>
      )}

      {detail.findings.length > 0 && (
        <div className={s.analysisFindings}>
          <div className={s.analysisFindingsTitle}>
            Findings ({detail.findings.length})
            {(detail.revoked_findings?.length ?? 0) > 0 && (
              <span className={s.analysisFindingsBadge}>
                • {detail.revoked_findings!.length} revoked
              </span>
            )}
          </div>
          {detail.findings.map((finding, i) => {
            const fb = feedbackItems[feedbackKey(finding)]
            // Conversation is locked while any item is active. We
            // compute this each render (findings list is small, so
            // O(n) scan is fine) rather than threading state.
            const conversationLocked = Object.values(feedbackItems).some(
              (x) => x.lesson_status === 'pending' || x.lesson_status === 'processing',
            )
            const identityKey = feedbackKey(finding)
            return (
              <div
                key={`${finding.request_id}::${finding.failure_mode}::${i}`}
                ref={(el) => { findingCardRefs.current[i] = el }}
              >
                <FindingCard
                  finding={finding}
                  feedback={fb}
                  conversationLocked={conversationLocked}
                  feedbackError={feedbackErrors[identityKey]}
                  reanalysisInFlight={reanalysisInFlight}
                  onSubmitFeedback={onSubmitFeedback}
                  onCancelFeedback={onCancelFeedback}
                  onDismissFeedbackError={() => onDismissFeedbackError(identityKey)}
                  {...linkedTextProps}
                />
              </div>
            )
          })}
        </div>
      )}

      {detail.findings.length === 0 && (
        <div className={s.analysisNoFindings}>
          No findings — clean conversation
          {(detail.revoked_findings?.length ?? 0) > 0 && (
            <span className={s.analysisFindingsBadge}>
              • {detail.revoked_findings!.length} revoked
            </span>
          )}
        </div>
      )}

      {(detail.revoked_findings?.length ?? 0) > 0 && (
        <RevokedFindingsSection revoked={detail.revoked_findings!} />
      )}

      {detail.notes && (
        <div className={s.analysisNotes}>
          <div className={s.analysisNotesTitle}>Notes</div>
          <div className={s.analysisNotesBody}>
            <LinkedText text={detail.notes} {...linkedTextProps} />
          </div>
        </div>
      )}

      {detail.history && detail.history.length > 0 && (
        <AnalysisHistoryView history={detail.history} />
      )}
    </div>
  )
}
