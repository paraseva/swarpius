import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSpeechRecognition } from './useSpeechRecognition'

// Mock SpeechRecognition
class MockSpeechRecognition {
  continuous = false
  interimResults = false
  lang = ''
  onresult: ((event: unknown) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onend: (() => void) | null = null
  start = vi.fn()
  stop = vi.fn()
  abort = vi.fn()
}

let lastInstance: MockSpeechRecognition | null = null

class CapturingMockSR extends MockSpeechRecognition {
  constructor() {
    super()
    lastInstance = this // eslint-disable-line @typescript-eslint/no-this-alias -- test helper
  }
}

function installCapturingMock() {
  lastInstance = null
  ;(globalThis as Record<string, unknown>).webkitSpeechRecognition = CapturingMockSR
}

function makeResultEvent(results: Array<{ transcript: string; isFinal: boolean }>) {
  const resultList = results.map(r => {
    const sr = Object.assign([{ transcript: r.transcript, confidence: 0.9 }], { isFinal: r.isFinal })
    return sr
  })
  return { resultIndex: 0, results: resultList }
}

describe('useSpeechRecognition', () => {
  let originalSR: unknown

  beforeEach(() => {
    originalSR = (globalThis as Record<string, unknown>).webkitSpeechRecognition
    ;(globalThis as Record<string, unknown>).webkitSpeechRecognition = MockSpeechRecognition
    lastInstance = null
  })

  afterEach(() => {
    if (originalSR === undefined) {
      delete (globalThis as Record<string, unknown>).webkitSpeechRecognition
    } else {
      ;(globalThis as Record<string, unknown>).webkitSpeechRecognition = originalSR
    }
    lastInstance = null
  })

  it('reports isSupported when SpeechRecognition API exists', () => {
    const { result } = renderHook(() => useSpeechRecognition())
    expect(result.current.isSupported).toBe(true)
  })

  it('reports not supported when API is missing', () => {
    delete (globalThis as Record<string, unknown>).webkitSpeechRecognition
    delete (globalThis as Record<string, unknown>).SpeechRecognition
    const { result } = renderHook(() => useSpeechRecognition())
    expect(result.current.isSupported).toBe(false)
  })

  it('is not listening initially', () => {
    const { result } = renderHook(() => useSpeechRecognition())
    expect(result.current.isListening).toBe(false)
  })

  it('starts listening on start()', () => {
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    expect(result.current.isListening).toBe(true)
  })

  it('stops listening on stop()', () => {
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    expect(result.current.isListening).toBe(true)
    act(() => result.current.stop())
    // isListening clears when onend fires
    expect(result.current.isListening).toBe(true) // still true until onend
  })

  it('provides interim transcript from recognition results', () => {
    installCapturingMock()
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    expect(lastInstance).not.toBeNull()

    act(() => {
      lastInstance!.onresult?.(makeResultEvent([
        { transcript: 'hello wor', isFinal: false },
      ]))
    })

    expect(result.current.interimTranscript).toBe('hello wor')
    expect(result.current.transcript).toBe('')
  })

  it('provides final transcript from recognition results', () => {
    installCapturingMock()
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())

    act(() => {
      lastInstance!.onresult?.(makeResultEvent([
        { transcript: 'hello world', isFinal: true },
      ]))
    })

    expect(result.current.transcript).toBe('hello world')
    expect(result.current.interimTranscript).toBe('')
  })

  it('clears isListening on onend', () => {
    installCapturingMock()
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    expect(result.current.isListening).toBe(true)

    act(() => lastInstance!.onend?.())
    expect(result.current.isListening).toBe(false)
  })

  it('sets error on recognition error', () => {
    installCapturingMock()
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())

    act(() => {
      lastInstance!.onerror?.({ error: 'not-allowed', message: 'Permission denied' })
    })

    expect(result.current.error).toBe('not-allowed')
  })

  it('clears isListening when onerror fires without a subsequent onend', () => {
    // Desktop Chrome reliably fires onend after onerror, but some mobile
    // browsers (iOS Safari, older Android WebView) do not. If only onerror
    // fires, isListening must still clear — otherwise the mic icon stays
    // lit with no way for the user to recover.
    installCapturingMock()
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    expect(result.current.isListening).toBe(true)

    act(() => {
      lastInstance!.onerror?.({ error: 'network', message: 'Network error' })
    })

    expect(result.current.isListening).toBe(false)
    expect(result.current.error).toBe('network')
  })

  it('resetTranscript clears both transcripts', () => {
    installCapturingMock()
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())

    act(() => {
      lastInstance!.onresult?.(makeResultEvent([
        { transcript: 'hello world', isFinal: true },
      ]))
    })

    expect(result.current.transcript).toBe('hello world')

    act(() => result.current.resetTranscript())
    expect(result.current.transcript).toBe('')
    expect(result.current.interimTranscript).toBe('')
  })

  it('does nothing when start() called on unsupported browser', () => {
    delete (globalThis as Record<string, unknown>).webkitSpeechRecognition
    delete (globalThis as Record<string, unknown>).SpeechRecognition
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    expect(result.current.isListening).toBe(false)
  })
})
