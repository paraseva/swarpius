import React from 'react'
import { useScrollToBottomButton } from '../hooks/useScrollToBottomButton'
import { ScrollToBottomButton } from './ScrollToBottomButton'

interface ScrollableViewportProps {
  /** Owned by the panel so its other scroll hooks attach to the same element. */
  scrollRef: React.RefObject<HTMLDivElement | null>
  /** Changes when content is appended at the bottom (e.g. the last item's id). */
  latestKey: string | number | undefined
  /** Applied to the inner scroll element (typically `panel-body scrollable`). */
  className?: string
  children: React.ReactNode
}

/** A scroll container with a transient jump-to-bottom affordance. The relative
 *  wrapper anchors the button to the viewport's bottom-right, above any footer. */
export const ScrollableViewport: React.FC<ScrollableViewportProps> = ({
  scrollRef, latestKey, className, children,
}) => {
  const { show, hasNew, scrollToBottom } = useScrollToBottomButton(scrollRef, latestKey)
  return (
    <div className="panel-scroll-affordance">
      <div ref={scrollRef} className={className}>
        {children}
      </div>
      <ScrollToBottomButton show={show} hasNew={hasNew} onClick={scrollToBottom} />
    </div>
  )
}
