/**
 * parseInboundPayload — the shape guard every inbound WS handler relies on.
 *
 * Contract: returns the parsed object when it satisfies the required shape
 * (a string request_id by default), otherwise null (and warns). Options can
 * relax the request_id requirement or additionally require a `type` field.
 * Non-object payloads short-circuit to null.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { parseInboundPayload } from './parseJson'

describe('parseInboundPayload', () => {
  afterEach(() => vi.restoreAllMocks())

  it('returns the object when request_id is a string', () => {
    const result = parseInboundPayload({ request_id: 'rq-1', value: 42 }, 'chat')
    expect(result).toEqual({ request_id: 'rq-1', value: 42 })
  })

  it('returns null and warns when request_id is missing', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseInboundPayload({ value: 42 }, 'chat')).toBeNull()
    expect(warn).toHaveBeenCalled()
  })

  it('returns null when request_id is present but not a string', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseInboundPayload({ request_id: 123 }, 'chat')).toBeNull()
  })

  it('accepts a payload without request_id when requireRequestId is false', () => {
    const result = parseInboundPayload(
      { value: 42 }, 'zone-snapshots', { requireRequestId: false },
    )
    expect(result).toEqual({ value: 42 })
  })

  it('requires a type field when requireType is set', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseInboundPayload(
      { request_id: 'rq-1' }, 'analysis',
      { requireRequestId: true, requireType: true },
    )).toBeNull()
    expect(parseInboundPayload(
      { request_id: 'rq-1', type: 'list' }, 'analysis',
      { requireRequestId: true, requireType: true },
    )).toEqual({ request_id: 'rq-1', type: 'list' })
  })

  it('returns null for a non-object payload', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseInboundPayload(42, 'chat')).toBeNull()
    expect(parseInboundPayload(null, 'chat')).toBeNull()
  })
})
