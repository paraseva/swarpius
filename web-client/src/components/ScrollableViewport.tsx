import React from 'react'
import { useScrollToBottomButton } from '../hooks/useScrollToBottomButton'
import { ScrollToBottomButton } from './ScrollToBottomButton'

interface ScrollableViewportProps {
  // Owned by the panel so its other scroll hooks attach to the same element.
  scrollRef: React.RefObject<HTMLDivElement | null>
  latestKey: string | number | undefined
  // Applied to the inner scroll element, not the wrapper.
  className?: string
  children: React.ReactNode
}

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
