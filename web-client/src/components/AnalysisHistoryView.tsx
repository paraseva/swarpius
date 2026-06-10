import React from 'react'
import { RequestIdBadge } from './RequestIdBadge'
import { RevokedFindingsSection } from './RevokedFindingsSection'
import { useScrollIntoViewOnExpand } from '../hooks/useScrollIntoViewOnExpand'
import {
  FEEDBACK_STATUS_CLASS,
  SEVERITY_COLOURS,
  type AnalysisFinding,
  type AnalysisHistoryEntry,
  type FeedbackItem,
} from './AnalysisBrowser.shared'
import s from './AnalysisBrowser.module.css'

export const AnalysisHistoryView: React.FC<{ history: AnalysisHistoryEntry[] }> = ({ history }) => {
  const [expanded, setExpanded] = React.useState(false)
  const reversedHistory = React.useMemo(() => [...history].reverse(), [history])
  const containerRef = useScrollIntoViewOnExpand<HTMLDivElement>(expanded)

  return (
    <div ref={containerRef} className={s.analysisHistory}>
      <button
        type="button"
        className={s.analysisHistoryToggle}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={s.analysisHistoryTitle}>
          Analysis History ({history.length} prior {history.length === 1 ? 'version' : 'versions'})
        </span>
        <svg className={`${s.analysisFindingChevron} ${expanded ? s.expanded : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      {expanded && reversedHistory.map((entry, i) => (
        <HistoryEntryCard
          key={entry.superseded_at ?? i}
          entry={entry}
          versionNumber={history.length - i}
        />
      ))}
    </div>
  )
}

const HistoryEntryCard: React.FC<{
  entry: AnalysisHistoryEntry
  versionNumber: number
}> = ({ entry, versionNumber }) => {
  const [expanded, setExpanded] = React.useState(false)
  const containerRef = useScrollIntoViewOnExpand<HTMLDivElement>(expanded)

  const severitySummary = React.useMemo(() => {
    const counts: Record<string, number> = {}
    for (const f of entry.findings) {
      counts[f.severity] = (counts[f.severity] || 0) + 1
    }
    return counts
  }, [entry.findings])

  const feedbackForFinding = React.useMemo(() => {
    return (finding: AnalysisFinding): FeedbackItem | undefined =>
      entry.feedback.find(
        fb => fb.request_id === finding.request_id && fb.failure_mode === finding.failure_mode,
      )
  }, [entry.feedback])

  const timestamp = entry.analysed_at.replace('T', ' ').replace(/Z$/, '').slice(0, 16)

  return (
    <div ref={containerRef} className={s.analysisHistoryEntry}>
      <button
        type="button"
        className={s.analysisHistoryEntryHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={s.analysisHistoryVersion}>v{versionNumber}</span>
        <span className={s.analysisHistoryTimestamp}>{timestamp}</span>
        <span className={s.analysisHistoryFindingCount}>
          {entry.findings.length} finding{entry.findings.length !== 1 ? 's' : ''}
          {(entry.revoked_findings?.length ?? 0) > 0 && (
            <span className={s.analysisFindingsBadge}>
              {' '}• {entry.revoked_findings!.length} revoked
            </span>
          )}
        </span>
        {Object.entries(severitySummary).map(([sev, count]) => (
          <span
            key={sev}
            className={s.analysisHistorySeverityBadge}
            style={{ color: SEVERITY_COLOURS[sev] }}
          >
            {count} {sev}
          </span>
        ))}
        {entry.feedback.length > 0 && (
          <span className={s.analysisHistoryFeedbackCount}>
            {entry.feedback.length} feedback
          </span>
        )}
        <svg className={`${s.analysisFindingChevron} ${expanded ? s.expanded : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      {expanded && (
        <div className={s.analysisHistoryEntryBody}>
          {entry.findings.length > 0 ? (
            entry.findings.map((finding, i) => (
              <HistoryFindingCard
                key={i}
                finding={finding}
                feedback={feedbackForFinding(finding)}
              />
            ))
          ) : (
            <div className={s.analysisNoFindings}>No findings</div>
          )}
          {(entry.revoked_findings?.length ?? 0) > 0 && (
            <RevokedFindingsSection revoked={entry.revoked_findings!} />
          )}
          {entry.notes && (
            <div className={s.analysisHistoryNotes}>{entry.notes}</div>
          )}
        </div>
      )}
    </div>
  )
}

const HistoryFindingCard: React.FC<{
  finding: AnalysisFinding
  feedback?: FeedbackItem
}> = ({ finding, feedback }) => {
  const [expanded, setExpanded] = React.useState(false)
  const severityColour = SEVERITY_COLOURS[finding.severity] ?? 'var(--color-text-secondary)'

  return (
    <div className={`${s.analysisFindingCard} ${s.history}`}>
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
            title={`${feedback.disposition} (${feedback.lesson_status})`}
          >
            {feedback.disposition}
          </span>
        )}
        <svg className={`${s.analysisFindingChevron} ${expanded ? s.expanded : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      <div className={s.analysisFindingSummary}>{finding.summary}</div>
      {expanded && (
        <>
          {finding.detail && (
            <div className={s.analysisFindingDetail}>{finding.detail}</div>
          )}
          {feedback && (
            <div className={s.analysisHistoryFeedbackInline}>
              <span className={s.analysisHistoryFeedbackDisposition}>{feedback.disposition}:</span>
              {' '}
              <span className={s.analysisHistoryFeedbackRebuttal}>{feedback.rebuttal}</span>
            </div>
          )}
        </>
      )}
    </div>
  )
}
