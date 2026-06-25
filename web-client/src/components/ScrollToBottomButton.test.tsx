import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ScrollToBottomButton } from './ScrollToBottomButton'

describe('ScrollToBottomButton', () => {
  it('is hidden from assistive tech and not focusable when not shown', () => {
    render(<ScrollToBottomButton show={false} hasNew={false} onClick={() => {}} />)
    const button = screen.getByRole('button', { hidden: true })
    expect(button).toHaveAttribute('aria-hidden', 'true')
    expect(button).toHaveAttribute('tabindex', '-1')
  })

  it('announces a plain jump-to-bottom action when shown without new content', () => {
    render(<ScrollToBottomButton show hasNew={false} onClick={() => {}} />)
    const button = screen.getByRole('button', { name: 'Scroll to bottom' })
    expect(button).toHaveAttribute('tabindex', '0')
  })

  it('announces new content when highlighted', () => {
    render(<ScrollToBottomButton show hasNew onClick={() => {}} />)
    expect(screen.getByRole('button', { name: /new messages/i })).toBeInTheDocument()
  })

  it('calls onClick when activated', async () => {
    const onClick = vi.fn()
    render(<ScrollToBottomButton show hasNew={false} onClick={onClick} />)
    await userEvent.click(screen.getByRole('button', { name: 'Scroll to bottom' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
