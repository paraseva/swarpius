import { describe, expect, it } from 'vitest'
import { parseMessageBody } from './formatMessageBody'

describe('parseMessageBody', () => {
  it('returns empty fields for blank input', () => {
    expect(parseMessageBody('   ')).toEqual({
      source: null,
      content: '',
      segments: null,
      parsedJson: null,
      hasPlanField: false,
      isChatResponse: false,
    })
  })

  it('extracts bracketed source header from first line', () => {
    const parsed = parseMessageBody('[Agent]\nhello there')
    expect(parsed.source).toBe('[Agent]')
    expect(parsed.content).toBe('hello there')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('handles source-only payload with no body content', () => {
    const parsed = parseMessageBody('[Agent]\n')
    expect(parsed.source).toBe('[Agent]')
    expect(parsed.content).toBe('')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('handles source payload with whitespace-only body', () => {
    const parsed = parseMessageBody('[Agent]\n   ')
    expect(parsed.source).toBe('[Agent]')
    expect(parsed.content).toBe('')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('keeps text as raw content when not JSON', () => {
    const parsed = parseMessageBody('plain text')
    expect(parsed.source).toBeNull()
    expect(parsed.content).toBe('plain text')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('parses JSON arrays without plan field', () => {
    const parsed = parseMessageBody('[1,2,3]')
    expect(parsed.parsedJson).toEqual([1, 2, 3])
    expect(parsed.hasPlanField).toBe(false)
  })

  it('detects plan field in JSON object payloads', () => {
    const parsed = parseMessageBody('{"plan":{"step":"do-x"},"other":true}')
    expect(parsed.parsedJson).toEqual({ plan: { step: 'do-x' }, other: true })
    expect(parsed.hasPlanField).toBe(true)
  })

  it('falls back to raw content when JSON parse fails', () => {
    const parsed = parseMessageBody('{not-valid-json}')
    expect(parsed.content).toBe('{not-valid-json}')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('strips leaked structured suffix from chat channel text', () => {
    const parsed = parseMessageBody(
      'Queued your tracks. \\"awaiting_user_response\\">false, \\"selected_skill\\":\\"roon_action\\"',
      'chat',
    )
    expect(parsed.content).toBe('Queued your tracks.')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('extracts chat_response from leaked JSON payload in chat channel', () => {
    const parsed = parseMessageBody(
      '{"chat_response":"I found two versions. Which one do you want?","awaiting_user_response":true,"selected_skill":null}',
      'chat',
    )
    expect(parsed.content).toBe('I found two versions. Which one do you want?')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('uses structured chat payload chat_response when provided', () => {
    const parsed = parseMessageBody('fallback', 'chat', {
      chat_response: 'Structured hello',
      selected_skill: 'roon_search',
    })
    expect(parsed.content).toBe('Structured hello')
    expect(parsed.parsedJson).toBeNull()
    expect(parsed.hasPlanField).toBe(false)
  })

  it('parses extended_info markup from chat_response', () => {
    const parsed = parseMessageBody('fallback', 'chat', {
      chat_response: 'Here are the results.\n\n<extended_info><summary>Track list</summary>1. Track A\n2. Track B</extended_info>',
    })
    expect(parsed.content).toContain('Here are the results.')
    expect(parsed.segments).toEqual([
      { type: 'text', content: 'Here are the results.' },
      { type: 'extended_info', content: '1. Track A\n2. Track B', summary: 'Track list' },
    ])
  })

  it('annotates tool output source with inferred tool name in tools channel', () => {
    const parsed = parseMessageBody(
      '[Coordinator Agent tool output]\n{"operation":"get_full_list","result_handle":"res_0001","items":[]}',
      'tool-outputs',
    )
    expect(parsed.source).toBe('[Coordinator Agent Result Fetch Tool output]')
  })

  it('annotates tool input source with inferred tool name in tools channel', () => {
    const parsed = parseMessageBody(
      '[Coordinator Agent tool input]\n{"zone":"Kitchen","result":"success"}',
      'tool-outputs',
    )
    expect(parsed.source).toBe('[Coordinator Agent Roon Action Tool input]')
  })

  it('does not annotate tool output source in non-tools channel', () => {
    const parsed = parseMessageBody(
      '[Coordinator Agent tool output]\n{"operation":"get_full_list","result_handle":"res_0001","items":[]}',
      'agent-outputs',
    )
    expect(parsed.source).toBe('[Coordinator Agent tool output]')
  })

  it('parses multiple extended_info blocks from chat_response', () => {
    const parsed = parseMessageBody('fallback', 'chat', {
      chat_response: '**Favourites** (3 tracks):\n\n<extended_info><summary>Favourites</summary>1. Track A\n2. Track B\n3. Track C</extended_info>\n\n**2000s** (2 tracks):\n\n<extended_info><summary>2000s</summary>1. Song X\n2. Song Y</extended_info>',
    })
    expect(parsed.segments).toHaveLength(4)
    expect(parsed.segments![0]).toEqual({ type: 'text', content: '**Favourites** (3 tracks):' })
    expect(parsed.segments![1]).toEqual({ type: 'extended_info', content: '1. Track A\n2. Track B\n3. Track C', summary: 'Favourites' })
    expect(parsed.segments![2]).toEqual({ type: 'text', content: '**2000s** (2 tracks):' })
    expect(parsed.segments![3]).toEqual({ type: 'extended_info', content: '1. Song X\n2. Song Y', summary: '2000s' })
  })

  it('returns null segments for plain text chat_response', () => {
    const parsed = parseMessageBody('fallback', 'chat', {
      chat_response: 'Here are the results.',
    })
    expect(parsed.segments).toBeNull()
  })

  it('returns segments null for non-chat channels', () => {
    const parsed = parseMessageBody('plain text', 'agent-outputs')
    expect(parsed.segments).toBeNull()
  })

  it('uses Details fallback when no summary tag', () => {
    const parsed = parseMessageBody('fallback', 'chat', {
      chat_response: 'Hello\n\n<extended_info>Some content</extended_info>',
    })
    expect(parsed.segments![1].summary).toBeUndefined()
  })
})
