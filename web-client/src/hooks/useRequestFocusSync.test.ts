import { describe, it, expect, beforeEach, vi } from 'vitest'
import { scrollRequestIntoView } from './useRequestFocusSync'

describe('scrollRequestIntoView', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  function makeItem(requestId: string, day: string): HTMLElement {
    const el = document.createElement('div')
    el.setAttribute('data-request-id', requestId)
    el.setAttribute('data-request-day', day)
    return el
  }

  it('disambiguates duplicate request ids across days by matching the day too', () => {
    // Conversation IDs reset daily, so rq-c04-0001 exists on both days.
    const container = document.createElement('div')
    container.scrollTo = vi.fn()
    const older = makeItem('rq-c04-0001', '2026-06-10')
    const newer = makeItem('rq-c04-0001', '2026-06-11')
    container.append(older, newer)
    document.body.append(container)

    const ok = scrollRequestIntoView(container, 'rq-c04-0001', '2026-06-11')

    expect(ok).toBe(true)
    expect(newer.classList.contains('request-focus-flash')).toBe(true)
    expect(older.classList.contains('request-focus-flash')).toBe(false)
  })

  it('falls back to id-only match when no day is given', () => {
    const container = document.createElement('div')
    container.scrollTo = vi.fn()
    const el = makeItem('rq-c01-0003', '2026-06-11')
    container.append(el)
    document.body.append(container)

    expect(scrollRequestIntoView(container, 'rq-c01-0003')).toBe(true)
    expect(el.classList.contains('request-focus-flash')).toBe(true)
  })
})
