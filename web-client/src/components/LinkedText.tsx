import React from 'react'
import { parseReferences, type TextSegment } from '../utils/parseReferences'
import { type ResultHandleData } from './AnalysisBrowser.shared'
import { CloseIcon } from './CloseIcon'
import s from './AnalysisBrowser.module.css'

const ResultHandleView: React.FC<{ data: ResultHandleData }> = ({ data }) => {
  return (
    <div className={s.rqLogViewer}>
      {data.search_history_line && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>Search History Entry</div>
          <pre className={s.rqLogPre}>{data.search_history_line}</pre>
        </div>
      )}
      {data.items && data.items.length > 0 && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>Cached Items ({data.items.length})</div>
          <pre className={s.rqLogPre}>{data.items.join('\n')}</pre>
        </div>
      )}
      {!data.items && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogKv}>result_fetch was not called for this handle during the conversation</div>
        </div>
      )}
    </div>
  )
}

export const LinkedText: React.FC<{
  text: string
  resultHandleCache: Record<string, ResultHandleData>
  resultHandleLoading: Record<string, boolean>
  onFetchResultHandle: (handle: string) => void
  onScrollToRequest?: (rqId: string) => void
}> = ({ text, resultHandleCache, resultHandleLoading, onFetchResultHandle, onScrollToRequest }) => {
  const [openRefs, setOpenRefs] = React.useState<Set<string>>(new Set())
  const segments = React.useMemo(() => parseReferences(text), [text])

  // Deduplicated list of unique references in this text (preserving order)
  const uniqueRefs = React.useMemo(() => {
    const seen = new Set<string>()
    const refs: TextSegment[] = []
    for (const seg of segments) {
      if (seg.type !== 'text' && !seen.has(seg.value)) {
        seen.add(seg.value)
        refs.push(seg)
      }
    }
    return refs
  }, [segments])

  // If no references, render plain text
  if (uniqueRefs.length === 0) return <>{text}</>

  const handleClick = (ref: string, type: 'request-id' | 'result-handle') => {
    if (type === 'request-id' && onScrollToRequest) {
      onScrollToRequest(ref)
      return
    }
    setOpenRefs((prev) => {
      const next = new Set(prev)
      if (next.has(ref)) {
        next.delete(ref)
      } else {
        next.add(ref)
        if (type === 'result-handle' && !resultHandleCache[ref] && !resultHandleLoading[ref]) {
          onFetchResultHandle(ref)
        }
      }
      return next
    })
  }

  return (
    <>
      <span className={s.linkedText}>
        {segments.map((seg, i) => {
          if (seg.type === 'text') return <React.Fragment key={i}>{seg.value}</React.Fragment>
          const isOpen = openRefs.has(seg.value)
          const refType = seg.type as 'request-id' | 'result-handle'
          const refClass = refType === 'result-handle' ? s.linkedRefResultHandle : ''
          return (
            <span
              key={i}
              role="button"
              tabIndex={0}
              className={`${s.linkedRef} ${refClass}${isOpen ? ` ${s.active}` : ''}`}
              onClick={() => handleClick(seg.value, refType)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleClick(seg.value, refType) }}
              title={refType === 'request-id' ? `Go to ${seg.value}` : `View ${seg.value}`}
            >
              {seg.value}
            </span>
          )
        })}
      </span>
      {openRefs.size > 0 && (
        <div className={s.linkedRefViewers}>
          {uniqueRefs.filter((r) => openRefs.has(r.value) && r.type === 'result-handle').map((ref) => {
            const data = resultHandleCache[ref.value]
            const loading = resultHandleLoading[ref.value]
            return (
              <div key={ref.value} className={s.linkedRefPanel}>
                <div className={s.linkedRefPanelHeader}>
                  <span>{ref.value}</span>
                  <button type="button" className="close-button" onClick={() => handleClick(ref.value, 'result-handle')} aria-label="Close"><CloseIcon /></button>
                </div>
                {loading && <div className={s.linkedRefLoading}>Loading...</div>}
                {data && <ResultHandleView data={data} />}
                {!loading && !data && <div className={s.linkedRefLoading}>No data</div>}
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
