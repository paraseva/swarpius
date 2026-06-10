import React from 'react'
import { RequestIdBadge } from './RequestIdBadge'
import { useScrollIntoViewOnExpand } from '../hooks/useScrollIntoViewOnExpand'
import { SEVERITY_COLOURS, type RevokedFinding } from './AnalysisBrowser.shared'
import s from './AnalysisBrowser.module.css'

export const RevokedFindingsSection: React.FC<{ revoked: RevokedFinding[] }> = ({ revoked }) => {
  const [expanded, setExpanded] = React.useState(false)
  const containerRef = useScrollIntoViewOnExpand<HTMLDivElement>(expanded)
  return (
    <div ref={containerRef} className={s.analysisRevoked}>
      <button
        type="button"
        className={s.analysisSectionToggle}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={`${s.analysisFindingsTitle} ${s.analysisRevokedTitle}`}>
          Revoked findings <span className={s.analysisRevokedCount}>({revoked.length})</span>
        </span>
        <svg
          className={`${s.analysisFindingChevron} ${expanded ? s.expanded : ''}`}
          viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round"
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      {expanded && revoked.map((r, i) => {
        const original = r.original_finding
        const severityColour = original ? SEVERITY_COLOURS[original.severity] : undefined
        return (
          <div key={r.id ?? i} className={s.analysisRevokedItem}>
            {original ? (
              <>
                <div className={s.analysisRevokedHeader}>
                  <span
                    className={s.analysisRevokedSeverity}
                    style={severityColour ? { color: severityColour } : undefined}
                  >
                    {original.severity}
                  </span>
                  <span className={s.analysisRevokedMode}>{original.failure_mode}</span>
                  <span className={s.analysisRevokedName}>{original.failure_name}</span>
                  <RequestIdBadge requestId={original.request_id} />
                </div>
                {original.summary && (
                  <div className={s.analysisRevokedSummary}>{original.summary}</div>
                )}
              </>
            ) : (
              <div className={s.analysisRevokedHeader}>
                <span className={s.analysisRevokedName}>(unmatched revocation, id: {r.id ?? '—'})</span>
              </div>
            )}
            <div className={s.analysisRevokedReason}>
              <span className={s.analysisRevokedReasonLabel}>Reason:</span>{r.reason}
            </div>
          </div>
        )
      })}
    </div>
  )
}
