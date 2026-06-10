import React from 'react'
import { RequestIdBadge } from './RequestIdBadge'
import { LinkedText } from './LinkedText'
import {
  FEEDBACK_STATUS_CLASS,
  SEVERITY_COLOURS,
  type AnalysisFinding,
  type FeedbackItem,
  type ResultHandleData,
} from './AnalysisBrowser.shared'
import s from './AnalysisBrowser.module.css'

const DISPOSITION_LABELS: Record<string, string> = {
  dismiss: 'Dismiss — finding is wrong',
  downgrade: 'Downgrade — severity is too high',
}

// User-facing labels for the feedback state machine. 'validated' is the
// on-disk value meaning "the lesson was extracted and the next
// re-analysis reflected it" — we surface that to users as 'Resolved'
// because the dispute is done, not because we've verified the
// operator's opinion.
const FEEDBACK_STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  processing: 'Processing',
  validated: 'Resolved',
  best_effort: 'Best effort',
  error: 'Error',
  orphaned: 'Orphaned',
}

export const FindingCard: React.FC<{
  finding: AnalysisFinding
  feedback?: FeedbackItem
  conversationLocked: boolean
  feedbackError?: string
  reanalysisInFlight: boolean
  onSubmitFeedback: (findingRequestId: string, failureMode: string, disposition: string, rebuttal: string) => void
  onCancelFeedback: (findingRequestId: string, failureMode: string) => void
  onDismissFeedbackError: () => void
  resultHandleCache: Record<string, ResultHandleData>
  resultHandleLoading: Record<string, boolean>
  onFetchResultHandle: (handle: string) => void
  onScrollToRequest: (rqId: string) => void
}> = ({ finding, feedback, conversationLocked, feedbackError, reanalysisInFlight, onSubmitFeedback, onCancelFeedback, onDismissFeedbackError, resultHandleCache, resultHandleLoading, onFetchResultHandle, onScrollToRequest }) => {
  const [expanded, setExpanded] = React.useState(false)
  const [showDisputeForm, setShowDisputeForm] = React.useState(false)
  const [disposition, setDisposition] = React.useState('dismiss')
  const [rebuttal, setRebuttal] = React.useState('')
  const severityColour = SEVERITY_COLOURS[finding.severity] ?? 'var(--color-text-secondary)'

  // Is *this* finding the one driving the lock?
  const isOwnPending = feedback?.lesson_status === 'pending'
  const isOwnProcessing = feedback?.lesson_status === 'processing'
  // Treat pending-plus-reanalysis-in-flight as processing from a UX
  // standpoint: we know the analyser is either already working on this
  // or about to, so the dispute is locked — cancel / refine would race
  // with the run. Backed by the operator's explicit Re-Analyse click,
  // which flips the flag until the response comes back.
  const effectivelyProcessing = isOwnProcessing || (isOwnPending && reanalysisInFlight)
  // The conv is locked and it's not by this finding → Dispute is disabled here.
  const blockedByOtherFinding = conversationLocked && !isOwnPending && !isOwnProcessing

  const handleSubmitDispute = () => {
    if (!rebuttal.trim()) return
    onSubmitFeedback(finding.request_id, finding.failure_mode, disposition, rebuttal.trim())
    setShowDisputeForm(false)
    setRebuttal('')
  }

  const handleCancel = () => {
    onCancelFeedback(finding.request_id, finding.failure_mode)
  }

  React.useEffect(() => {
    if (!showDisputeForm) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowDisputeForm(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showDisputeForm])

  const linkedTextProps = {
    resultHandleCache, resultHandleLoading, onFetchResultHandle,
    onScrollToRequest,
  }

  return (
    <div className={s.analysisFindingCard}>
      <button
        type="button"
        className={s.analysisFindingHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={s.analysisFindingSeverity} style={{ color: severityColour }}>
          {finding.severity}
        </span>
        <span className={s.analysisFindingMode}>{finding.failure_mode}</span>
        <span className={s.analysisFindingName}>{finding.failure_name}</span>
        <RequestIdBadge requestId={finding.request_id} />
        {feedback && (
          <span
            className={`${s.analysisFeedbackStatus} ${FEEDBACK_STATUS_CLASS[feedback.lesson_status] ?? ''}`}
            title={feedback.lesson_status === 'pending'
              ? 'Feedback received — will be processed on the next analysis run'
              : `Feedback: ${feedback.disposition} (${feedback.lesson_status})`}
          >
            {FEEDBACK_STATUS_LABELS[feedback.lesson_status] ?? feedback.lesson_status}
          </span>
        )}
        <svg
          className={`${s.analysisFindingChevron} ${expanded ? s.expanded : ''}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      <div className={s.analysisFindingSummary}>
        <LinkedText text={finding.summary} {...linkedTextProps} />
      </div>
      {expanded && (
        <>
          {finding.detail && (
            <div className={s.analysisFindingDetail}>
              <LinkedText text={finding.detail} {...linkedTextProps} />
            </div>
          )}
          <div className={s.analysisFindingActions}>
            <button
              type="button"
              className={s.rqLogToggle}
              onClick={() => onScrollToRequest(finding.request_id)}
            >
              Go to request
            </button>
            {!feedback && (
              <button
                type="button"
                className={s.rqLogToggle}
                onClick={() => setShowDisputeForm(!showDisputeForm)}
                disabled={blockedByOtherFinding}
                title={blockedByOtherFinding
                  ? 'Another dispute on this conversation is pending — wait for it to be processed'
                  : undefined}
              >
                {showDisputeForm && !blockedByOtherFinding ? 'Cancel' : 'Dispute'}
              </button>
            )}
            {isOwnPending && !effectivelyProcessing && (
              <button
                type="button"
                className={s.rqLogToggle}
                onClick={handleCancel}
              >
                Cancel dispute
              </button>
            )}
          </div>
          {showDisputeForm && !feedback && !blockedByOtherFinding && (
            <div className={s.analysisDisputeForm}>
              <label>
                Disposition
                <select value={disposition} onChange={(e) => setDisposition(e.target.value)}>
                  {Object.entries(DISPOSITION_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <label>
                Rebuttal
                <textarea
                  value={rebuttal}
                  onChange={(e) => setRebuttal(e.target.value)}
                  placeholder="Explain why this finding is incorrect or needs adjustment..."
                  rows={3}
                />
              </label>
              <button
                type="button"
                className={s.analysisDisputeSubmit}
                onClick={handleSubmitDispute}
                disabled={!rebuttal.trim()}
              >
                Submit feedback
              </button>
            </div>
          )}
          {feedback && (isOwnPending || isOwnProcessing) && (
            <div className={s.analysisHistoryFeedbackInline}>
              <span className={s.analysisHistoryFeedbackDisposition}>
                {feedback.disposition}
                {effectivelyProcessing ? ' (re-analysis in progress)' : ''}
                :
              </span>
              {' '}
              <span className={s.analysisHistoryFeedbackRebuttal}>{feedback.rebuttal}</span>
            </div>
          )}
          {feedbackError && (
            <div className={s.analysisFeedbackError} role="alert">
              <span className={s.analysisFeedbackErrorMessage}>{feedbackError}</span>
              <button
                type="button"
                className={s.analysisFeedbackErrorDismiss}
                onClick={onDismissFeedbackError}
                aria-label="Dismiss error"
              >
                ×
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
