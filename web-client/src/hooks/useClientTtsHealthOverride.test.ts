import { renderHook, act } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TTS_ERROR_EVENT_NAME, TTS_RECOVERED_EVENT_NAME } from '../tts'
import { useClientTtsHealthOverride } from './useClientTtsHealthOverride'

describe('useClientTtsHealthOverride', () => {
  it('defaults to false', () => {
    const { result } = renderHook(() => useClientTtsHealthOverride())
    expect(result.current).toBe(false)
  })

  it('flips to true when the TTS error event fires', () => {
    const { result } = renderHook(() => useClientTtsHealthOverride())
    act(() => {
      window.dispatchEvent(new CustomEvent(TTS_ERROR_EVENT_NAME))
    })
    expect(result.current).toBe(true)
  })

  it('flips back to false when the TTS recovered event fires', () => {
    const { result } = renderHook(() => useClientTtsHealthOverride())
    act(() => {
      window.dispatchEvent(new CustomEvent(TTS_ERROR_EVENT_NAME))
    })
    expect(result.current).toBe(true)
    act(() => {
      window.dispatchEvent(new CustomEvent(TTS_RECOVERED_EVENT_NAME))
    })
    expect(result.current).toBe(false)
  })

  it('detaches listeners on unmount', () => {
    const { result, unmount } = renderHook(() => useClientTtsHealthOverride())
    unmount()
    window.dispatchEvent(new CustomEvent(TTS_ERROR_EVENT_NAME))
    const { result: fresh } = renderHook(() => useClientTtsHealthOverride())
    expect(result.current).toBe(false)
    expect(fresh.current).toBe(false)
  })
})
