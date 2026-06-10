import { renderHook, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStepLabel } from './useChatStepLabel'
import type { SocketMessage } from '../websocketContext'

const agentEvent = (
  payload: Record<string, unknown>,
  id: string = `m-${Math.random()}`,
): SocketMessage => ({
  id,
  channel: 'agent-outputs',
  direction: 'inbound',
  body: '',
  payload,
  timestamp: 0,
})

const toolStarted = (
  toolCallId: string,
  toolName: string,
  displayLabel: string,
  id: string = `m-${Math.random()}`,
): SocketMessage => agentEvent({
  event_type: 'tool_call_started',
  tool_call_id: toolCallId,
  tool_name: toolName,
  display_label: displayLabel,
}, id)

const toolCompleted = (
  toolCallId: string,
  toolName: string,
  id: string = `m-${Math.random()}`,
): SocketMessage => agentEvent({
  event_type: 'tool_call_completed',
  tool_call_id: toolCallId,
  tool_name: toolName,
}, id)

describe('useChatStepLabel', () => {
  it('returns no steps when no events have been seen', () => {
    const { result } = renderHook(() => useChatStepLabel([], 0))
    expect(result.current.steps).toEqual([])
  })

  it('shows "Classifying..." while diagnostic agent is active', () => {
    const messages = [agentEvent({ event_type: 'diagnostic_active' })]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toHaveLength(1)
    expect(result.current.steps[0]).toMatchObject({
      label: 'Classifying...',
      isActive: true,
    })
  })

  it('replaces "Classifying..." with "Thinking..." on request_id_assignment', () => {
    const messages = [
      agentEvent({ event_type: 'diagnostic_active' }),
      agentEvent({ event_type: 'request_id_assignment', request_id: 'rq-c01-0001' }),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toHaveLength(1)
    expect(result.current.steps[0].label).toBe('Thinking...')
  })

  it('shows the display_label as an active step when a tool starts', () => {
    const messages = [toolStarted('call_1', 'roon_search', 'Searching library')]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toHaveLength(1)
    expect(result.current.steps[0]).toMatchObject({
      label: 'Searching library',
      isActive: true,
    })
  })

  it('falls back to "Running <tool>" when no display_label is provided', () => {
    const messages = [
      agentEvent({
        event_type: 'tool_call_started',
        tool_call_id: 'call_1',
        tool_name: 'roon_action',
      }),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps[0].label).toBe('Running roon_action')
  })

  it('replaces the "Thinking..." placeholder when a real tool step starts', () => {
    const messages = [
      agentEvent({ event_type: 'request_id_assignment', request_id: 'rq-c01-0001' }, 'a'),
      toolStarted('call_1', 'roon_search', 'Searching library', 'b'),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toHaveLength(1)
    expect(result.current.steps[0]).toMatchObject({
      label: 'Searching library',
      isActive: true,
    })
  })

  it('freezes a sequential tool when the next sequential tool starts', () => {
    // Tool A completes (inserts a "Thinking..." placeholder); tool B
    // starts and replaces the placeholder. Trail: A frozen, B active.
    const messages = [
      toolStarted('call_a', 'roon_search', 'Searching library', '1'),
      toolCompleted('call_a', 'roon_search', '2'),
      toolStarted('call_b', 'web_search', 'Searching the web', '3'),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toHaveLength(2)
    expect(result.current.steps[0]).toMatchObject({
      label: 'Searching library',
      isActive: false,
    })
    expect(result.current.steps[1]).toMatchObject({
      label: 'Searching the web',
      isActive: true,
    })
  })

  it('marks the last tool as done when the loop ends with done=true', () => {
    const messages = [
      toolStarted('call_a', 'roon_search', 'Searching library', '1'),
      toolCompleted('call_a', 'roon_search', '2'),
      agentEvent({
        event_type: 'coordinator_step',
        selected_skill: null,
        done: true,
      }, '3'),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    // tool_call_completed inserted a "Thinking..." placeholder; the
    // done=true terminator drops it and freezes any remaining actives.
    expect(result.current.steps).toHaveLength(1)
    expect(result.current.steps[0]).toMatchObject({
      label: 'Searching library',
      isActive: false,
    })
  })

  it("drops a trailing placeholder on done=true so it doesn't freeze in the trail", () => {
    const messages = [
      agentEvent({ event_type: 'request_id_assignment', request_id: 'rq-c01-0001' }, 'a'),
      agentEvent({
        event_type: 'coordinator_step',
        selected_skill: null,
        done: true,
      }, 'b'),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toEqual([])
  })

  it('flips to "Thinking..." on tool_call_completed', () => {
    // Single-tool scenario: tool starts, tool completes — trail shows
    // the tool frozen and a "Thinking..." placeholder active.
    const messages = [
      toolStarted('call_1', 'roon_action', 'Controlling playback', 'a'),
      toolCompleted('call_1', 'roon_action', 'b'),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toHaveLength(2)
    expect(result.current.steps[0]).toMatchObject({
      label: 'Controlling playback',
      isActive: false,
    })
    expect(result.current.steps[1]).toMatchObject({
      label: 'Thinking...',
      isActive: true,
    })
  })

  it('resets steps on request_complete', () => {
    const messages = [
      toolStarted('call_1', 'roon_search', 'Searching library', 'a'),
      agentEvent({ event_type: 'request_complete' }, 'b'),
    ]
    const { result } = renderHook(() => useChatStepLabel(messages, 0))
    expect(result.current.steps).toEqual([])
  })

  describe('parallel tools', () => {
    it('appends a separate active entry per concurrent tool_call_started', () => {
      const messages = [
        toolStarted('call_a', 'web_search', 'Searching the web', '1'),
        toolStarted('call_b', 'roon_search', 'Searching library', '2'),
      ]
      const { result } = renderHook(() => useChatStepLabel(messages, 0))
      expect(result.current.steps).toHaveLength(2)
      expect(result.current.steps[0]).toMatchObject({
        label: 'Searching the web',
        isActive: true,
      })
      expect(result.current.steps[1]).toMatchObject({
        label: 'Searching library',
        isActive: true,
      })
    })

    it('freezes a specific parallel entry by tool_call_id on completion', () => {
      const messages = [
        toolStarted('call_a', 'web_search', 'Searching the web', '1'),
        toolStarted('call_b', 'roon_search', 'Searching library', '2'),
        toolCompleted('call_a', 'web_search', '3'),
      ]
      const { result } = renderHook(() => useChatStepLabel(messages, 0))
      expect(result.current.steps).toHaveLength(2)
      expect(result.current.steps[0]).toMatchObject({
        label: 'Searching the web',
        isActive: false,
      })
      expect(result.current.steps[1]).toMatchObject({
        label: 'Searching library',
        isActive: true,
      })
    })

    it('does not flip to "Thinking..." until every parallel tool completes', () => {
      const messages = [
        toolStarted('call_a', 'web_search', 'Searching the web', '1'),
        toolStarted('call_b', 'roon_search', 'Searching library', '2'),
        toolCompleted('call_a', 'web_search', '3'),
      ]
      const { result } = renderHook(() => useChatStepLabel(messages, 0))
      // Searching library still active — no Thinking placeholder yet.
      expect(result.current.steps.some((s) => s.label === 'Thinking...')).toBe(false)
    })

    it('appends "Thinking..." after the last parallel tool completes', () => {
      const messages = [
        toolStarted('call_a', 'web_search', 'Searching the web', '1'),
        toolStarted('call_b', 'roon_search', 'Searching library', '2'),
        toolCompleted('call_a', 'web_search', '3'),
        toolCompleted('call_b', 'roon_search', '4'),
      ]
      const { result } = renderHook(() => useChatStepLabel(messages, 0))
      expect(result.current.steps).toHaveLength(3)
      expect(result.current.steps[0]).toMatchObject({
        label: 'Searching the web',
        isActive: false,
      })
      expect(result.current.steps[1]).toMatchObject({
        label: 'Searching library',
        isActive: false,
      })
      expect(result.current.steps[2]).toMatchObject({
        label: 'Thinking...',
        isActive: true,
      })
    })

    it('ignores tool_call_completed with unknown tool_call_id without crashing', () => {
      // Defensive: WS reconnect mid-request could lose the started event.
      const messages = [
        toolCompleted('ghost', 'mystery', '1'),
      ]
      const { result } = renderHook(() => useChatStepLabel(messages, 0))
      // No tool entries; the completion still inserts a Thinking
      // placeholder because there are no active tools.
      expect(result.current.steps).toHaveLength(1)
      expect(result.current.steps[0]).toMatchObject({
        label: 'Thinking...',
        isActive: true,
      })
    })
  })

  describe('per-step elapsed time', () => {
    beforeEach(() => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-05-01T12:00:00Z'))
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('ticks elapsedSec on the active step once per second', () => {
      const messages = [toolStarted('call_1', 'roon_search', 'Searching library')]
      const { result } = renderHook(() => useChatStepLabel(messages, 0))
      expect(result.current.steps[0].elapsedSec).toBe(0)

      act(() => { vi.advanceTimersByTime(2_500) })
      expect(result.current.steps[0].elapsedSec).toBe(2)

      act(() => { vi.advanceTimersByTime(1_000) })
      expect(result.current.steps[0].elapsedSec).toBe(3)
    })

    it('freezes elapsedSec on a step when the next sequential step starts', () => {
      const initial: SocketMessage[] = [
        toolStarted('call_a', 'roon_search', 'Searching library', 'a'),
      ]
      const { result, rerender } = renderHook(
        ({ msgs }: { msgs: SocketMessage[] }) => useChatStepLabel(msgs, 0),
        { initialProps: { msgs: initial } },
      )

      act(() => { vi.advanceTimersByTime(3_000) })
      expect(result.current.steps[0].elapsedSec).toBe(3)

      rerender({
        msgs: [
          ...initial,
          toolCompleted('call_a', 'roon_search', 'b'),
          toolStarted('call_b', 'web_search', 'Searching the web', 'c'),
        ],
      })

      act(() => { vi.advanceTimersByTime(5_000) })

      expect(result.current.steps[0]).toMatchObject({
        label: 'Searching library',
        elapsedSec: 3,
        isActive: false,
      })
      expect(result.current.steps[1]).toMatchObject({
        label: 'Searching the web',
        elapsedSec: 5,
        isActive: true,
      })
    })

    it("re-aligns the interval to the active step's startedAt on each transition", () => {
      const initial: SocketMessage[] = [
        toolStarted('call_a', 'roon_search', 'Searching library', 'a'),
      ]
      const { result, rerender } = renderHook(
        ({ msgs }: { msgs: SocketMessage[] }) => useChatStepLabel(msgs, 0),
        { initialProps: { msgs: initial } },
      )

      act(() => { vi.advanceTimersByTime(1_500) })

      rerender({
        msgs: [
          ...initial,
          toolCompleted('call_a', 'roon_search', 'b'),
          toolStarted('call_b', 'web_search', 'Searching the web', 'c'),
        ],
      })
      expect(result.current.steps[1].elapsedSec).toBe(0)

      act(() => { vi.advanceTimersByTime(1_000) })
      expect(result.current.steps[1].elapsedSec).toBe(1)
    })

    it('parallel actives tick independently from their own startedAt', () => {
      const initial: SocketMessage[] = [
        toolStarted('call_a', 'web_search', 'Searching the web', 'a'),
      ]
      const { result, rerender } = renderHook(
        ({ msgs }: { msgs: SocketMessage[] }) => useChatStepLabel(msgs, 0),
        { initialProps: { msgs: initial } },
      )

      // 2 seconds pass before second tool starts.
      act(() => { vi.advanceTimersByTime(2_000) })

      rerender({
        msgs: [
          ...initial,
          toolStarted('call_b', 'roon_search', 'Searching library', 'b'),
        ],
      })

      // Both active.
      act(() => { vi.advanceTimersByTime(3_000) })

      expect(result.current.steps[0]).toMatchObject({
        label: 'Searching the web',
        elapsedSec: 5,
        isActive: true,
      })
      expect(result.current.steps[1]).toMatchObject({
        label: 'Searching library',
        elapsedSec: 3,
        isActive: true,
      })
    })

    it('clears all entries on request_complete', () => {
      const initial: SocketMessage[] = [
        agentEvent({ event_type: 'diagnostic_active' }, 'a'),
      ]
      const { result, rerender } = renderHook(
        ({ msgs }: { msgs: SocketMessage[] }) => useChatStepLabel(msgs, 0),
        { initialProps: { msgs: initial } },
      )
      act(() => { vi.advanceTimersByTime(2_000) })
      expect(result.current.steps[0].elapsedSec).toBe(2)

      rerender({
        msgs: [...initial, agentEvent({ event_type: 'request_complete' }, 'b')],
      })
      expect(result.current.steps).toEqual([])

      act(() => { vi.advanceTimersByTime(5_000) })
      expect(result.current.steps).toEqual([])
    })
  })
})
