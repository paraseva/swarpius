import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, beforeEach } from 'vitest'
import { useDevMode } from './useDevMode'

const STORAGE_KEY = 'swarpius:dev-mode'

describe('useDevMode', () => {
  beforeEach(() => {
    localStorage.removeItem(STORAGE_KEY)
  })

  it('defaults to false when no localStorage entry', () => {
    const { result } = renderHook(() => useDevMode())
    expect(result.current.isDevMode).toBe(false)
  })

  it('reads true from localStorage', () => {
    localStorage.setItem(STORAGE_KEY, '1')
    const { result } = renderHook(() => useDevMode())
    expect(result.current.isDevMode).toBe(true)
  })

  it('reads false from localStorage', () => {
    localStorage.setItem(STORAGE_KEY, '0')
    const { result } = renderHook(() => useDevMode())
    expect(result.current.isDevMode).toBe(false)
  })

  it('toggleDevMode flips state and persists', () => {
    const { result } = renderHook(() => useDevMode())
    expect(result.current.isDevMode).toBe(false)

    act(() => result.current.toggleDevMode())
    expect(result.current.isDevMode).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('1')

    act(() => result.current.toggleDevMode())
    expect(result.current.isDevMode).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('0')
  })
})
