import { act, configure, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isNewerVersion, parseSemver, useUpdateCheck, useUpdateCheckEnabled } from './updateCheck'

function mockFetchTag(tag: string | null, ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    json: async () => ({ tag_name: tag }),
  })
}

describe('parseSemver', () => {
  it('parses a plain version', () => {
    expect(parseSemver('1.2.3')).toEqual([1, 2, 3])
  })

  it('tolerates a leading v and a pre-release suffix', () => {
    expect(parseSemver('v1.2.3')).toEqual([1, 2, 3])
    expect(parseSemver('1.2.3-beta.1')).toEqual([1, 2, 3])
  })

  it('returns null for junk', () => {
    expect(parseSemver('not-a-version')).toBeNull()
    expect(parseSemver('')).toBeNull()
  })
})

describe('isNewerVersion', () => {
  it('detects a newer patch / minor / major', () => {
    expect(isNewerVersion('1.0.0', '1.0.1')).toBe(true)
    expect(isNewerVersion('1.0.0', '1.1.0')).toBe(true)
    expect(isNewerVersion('1.9.9', '2.0.0')).toBe(true)
  })

  it('returns false for same or older', () => {
    expect(isNewerVersion('1.0.0', '1.0.0')).toBe(false)
    expect(isNewerVersion('1.0.1', '1.0.0')).toBe(false)
    expect(isNewerVersion('2.0.0', '1.9.9')).toBe(false)
  })

  it('tolerates a leading v on either side', () => {
    expect(isNewerVersion('1.0.0', 'v1.0.1')).toBe(true)
    expect(isNewerVersion('v1.0.0', '1.0.0')).toBe(false)
  })

  it('never reports an update when either version is unparseable', () => {
    // Guards the "render nothing on a bad response" contract — a junk
    // tag_name must not surface a phantom update.
    expect(isNewerVersion('1.0.0', 'garbage')).toBe(false)
    expect(isNewerVersion('1.0.0', '')).toBe(false)
    expect(isNewerVersion('', '2.0.0')).toBe(false)
  })
})

describe('useUpdateCheck', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('surfaces a newer release as available after the auto-check', async () => {
    vi.stubGlobal('fetch', mockFetchTag('v1.2.0'))
    const { result } = renderHook(() => useUpdateCheck(true, '1.0.0'))
    await waitFor(() => expect(result.current.available).toBe('v1.2.0'))
    expect(result.current.checking).toBe(false)
  })

  it('reports no update when the latest equals the current version', async () => {
    const fetchMock = mockFetchTag('v1.0.0')
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useUpdateCheck(true, '1.0.0'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(result.current.available).toBeNull()
  })

  it('skips the auto-check when disabled but still honours a manual checkNow', async () => {
    const fetchMock = mockFetchTag('v2.0.0')
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useUpdateCheck(false, '1.0.0'))
    await waitFor(() => expect(result.current.checking).toBe(false))
    expect(fetchMock).not.toHaveBeenCalled()
    await act(async () => {
      result.current.checkNow()
    })
    await waitFor(() => expect(result.current.available).toBe('v2.0.0'))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('checkNow bypasses a fresh cache and forces a network fetch', async () => {
    localStorage.setItem(
      'swarpius:update-check',
      JSON.stringify({ latest: 'v1.0.0', ts: Date.now() }),
    )
    const fetchMock = mockFetchTag('v3.0.0')
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useUpdateCheck(true, '1.0.0'))
    await waitFor(() => expect(result.current.available).toBeNull())
    expect(fetchMock).not.toHaveBeenCalled()
    await act(async () => {
      result.current.checkNow()
    })
    await waitFor(() => expect(result.current.available).toBe('v3.0.0'))
  })

  it('reverts checking after a 404 even under a StrictMode double-invoke', async () => {
    // StrictMode double-invokes effects in dev (the app wraps in it), which
    // exposed a mounted-ref guard that never reset to true and left
    // "Checking…" stuck on a 404.
    configure({ reactStrictMode: true })
    try {
      vi.stubGlobal('fetch', mockFetchTag(null, false))
      const { result } = renderHook(() => useUpdateCheck(false, '1.0.0'))
      await act(async () => {
        result.current.checkNow()
      })
      await waitFor(() => expect(result.current.checking).toBe(false))
    } finally {
      configure({ reactStrictMode: false })
    }
  })

  it('stops checking when the request never settles (times out)', async () => {
    vi.useFakeTimers()
    // A fetch that only ever settles if its abort signal fires — i.e. a hung
    // request (host blocked/unreachable), unlike a clean 404 that resolves.
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, opts?: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          )
        }),
      ),
    )
    const { result } = renderHook(() => useUpdateCheck(false, '1.0.0'))
    act(() => {
      result.current.checkNow()
    })
    expect(result.current.checking).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(result.current.checking).toBe(false)
  })
})

describe('useUpdateCheckEnabled', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to enabled (opt-out) when nothing is stored', () => {
    const { result } = renderHook(() => useUpdateCheckEnabled())
    expect(result.current.enabled).toBe(true)
  })

  it('persists the preference across mounts', () => {
    const first = renderHook(() => useUpdateCheckEnabled())
    act(() => {
      first.result.current.setEnabled(false)
    })
    expect(first.result.current.enabled).toBe(false)
    first.unmount()

    // A fresh mount (e.g. a page reload) reflects the saved opt-out.
    const second = renderHook(() => useUpdateCheckEnabled())
    expect(second.result.current.enabled).toBe(false)

    act(() => {
      second.result.current.setEnabled(true)
    })
    second.unmount()
    expect(renderHook(() => useUpdateCheckEnabled()).result.current.enabled).toBe(true)
  })
})
