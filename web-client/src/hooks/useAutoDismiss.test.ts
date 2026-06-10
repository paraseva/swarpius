import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAutoDismiss } from './useAutoDismiss'

afterEach(() => {
  vi.useRealTimers()
})

describe('useAutoDismiss', () => {
  it('calls onExpire once the value has been active for the delay', () => {
    vi.useFakeTimers()
    const onExpire = vi.fn()
    renderHook(() => useAutoDismiss(true, onExpire, 5000))
    vi.advanceTimersByTime(4999)
    expect(onExpire).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(onExpire).toHaveBeenCalledTimes(1)
  })

  it('never fires while inactive', () => {
    vi.useFakeTimers()
    const onExpire = vi.fn()
    renderHook(() => useAutoDismiss(false, onExpire, 5000))
    vi.advanceTimersByTime(10000)
    expect(onExpire).not.toHaveBeenCalled()
  })

  it('cancels the pending timer when the value goes inactive', () => {
    vi.useFakeTimers()
    const onExpire = vi.fn()
    const { rerender } = renderHook(
      ({ active }) => useAutoDismiss(active, onExpire, 5000),
      { initialProps: { active: true } },
    )
    vi.advanceTimersByTime(3000)
    rerender({ active: false })
    vi.advanceTimersByTime(5000)
    expect(onExpire).not.toHaveBeenCalled()
  })
})
