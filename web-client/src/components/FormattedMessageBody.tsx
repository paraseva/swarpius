import React from 'react'
import s from './FormattedMessageBody.module.css'
import { parseMessageBody, type DetailSegment } from '../utils/formatMessageBody'
import { type ChannelId } from '../websocketContext'
import { JsonTreeView } from './JsonTreeView'
import { MarkdownText } from './MarkdownText'

// Inbound Swarpius responses render through markdown; everything else
// (outbound user text, diagnostics channels) stays verbatim so users
// see exactly what was produced.
const ChatBubbleText: React.FC<{ text: string; isChatResponse: boolean }> = ({ text, isChatResponse }) =>
  isChatResponse ? <MarkdownText>{text}</MarkdownText> : <pre className="message-pre">{text}</pre>

const DetailedInfoSection: React.FC<{ text: string; summary?: string; isChatResponse: boolean }> = ({
  text,
  summary,
  isChatResponse,
}) => {
  const [collapsed, setCollapsed] = React.useState(false)

  return (
    <div className="detailed-info-section">
      <button
        type="button"
        className="detailed-info-toggle"
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
      >
        <svg className="detailed-info-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
        {summary || 'Details'}
      </button>
      {!collapsed ? (
        <div className="detailed-info-content">
          <ChatBubbleText text={text} isChatResponse={isChatResponse} />
        </div>
      ) : null}
    </div>
  )
}

const ListSection: React.FC<{ segment: DetailSegment; isChatResponse: boolean }> = ({ segment, isChatResponse }) => {
  const [collapsed, setCollapsed] = React.useState(false)
  const hasChildren = segment.children && segment.children.length > 0

  return (
    <div className={s.listSection}>
      <button
        type="button"
        className={s.listSectionToggle}
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
      >
        <svg className={s.listSectionChevron} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
        {segment.summary || 'List'}
      </button>
      {!collapsed ? (
        <div className={s.listSectionContent}>
          {hasChildren ? (
            segment.children!.map((child, idx) =>
              child.type === 'list' ? (
                <ListSection key={idx} segment={child} isChatResponse={isChatResponse} />
              ) : child.type === 'text' && child.content ? (
                <ChatBubbleText key={idx} text={child.content} isChatResponse={isChatResponse} />
              ) : null,
            )
          ) : segment.content ? (
            <ChatBubbleText text={segment.content} isChatResponse={isChatResponse} />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

const SegmentRenderer: React.FC<{ segments: DetailSegment[]; isChatResponse: boolean }> = ({
  segments,
  isChatResponse,
}) => (
  <>
    {segments.map((seg, idx) =>
      seg.type === 'extended_info' ? (
        <DetailedInfoSection key={idx} text={seg.content} summary={seg.summary} isChatResponse={isChatResponse} />
      ) : seg.type === 'list' ? (
        <ListSection key={idx} segment={seg} isChatResponse={isChatResponse} />
      ) : (
        <ChatBubbleText key={idx} text={seg.content} isChatResponse={isChatResponse} />
      ),
    )}
  </>
)

export const FormattedMessageBody: React.FC<{ body: string; channel?: ChannelId; payload?: unknown }> = ({
  body,
  channel,
  payload,
}) => {
  const { source, content, segments, parsedJson, hasPlanField, isChatResponse } = parseMessageBody(body, channel, payload)
  if (!content && source === null && !segments) return <span className="message-body" />

  return (
    <div className="message-body formatted">
      {source && (
        <h6 className="message-source">
          {source}
        </h6>
      )}
      {segments ? (
        <SegmentRenderer segments={segments} isChatResponse={isChatResponse} />
      ) : hasPlanField ? (
        <>
          <div className="message-block">
            <div className="message-block-label">Plan</div>
            <JsonTreeView data={(parsedJson as { plan?: unknown }).plan} className="message-tree" />
          </div>
          <div className="message-block">
            <div className="message-block-label">Full output</div>
            <JsonTreeView data={parsedJson} className="message-tree" />
          </div>
        </>
      ) : parsedJson !== null ? (
        <JsonTreeView data={parsedJson} className="message-tree" />
      ) : (
        <ChatBubbleText text={content} isChatResponse={isChatResponse} />
      )}
    </div>
  )
}
