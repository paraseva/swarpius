import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi, type Mock } from 'vitest'
import { useRoonCommands } from './useRoonCommands'

type SendMock = Mock<(channel: string, body: string) => string>

const makeSend = (): SendMock => vi.fn<(channel: string, body: string) => string>(() => 'rq')

const lastSent = (sendMessage: SendMock) => {
  const calls = sendMessage.mock.calls
  const last = calls[calls.length - 1]
  if (!last) throw new Error('sendMessage was not called')
  return { channel: last[0], payload: JSON.parse(last[1]) as Record<string, unknown> }
}

describe('useRoonCommands', () => {
  it('zoneCommand emits a roon-control-request with action + zone for transport commands', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.zoneCommand('Kitchen', 'play')

    const { channel, payload } = lastSent(sendMessage)
    expect(channel).toBe('roon-control-request')
    expect(payload).toMatchObject({ action: 'play', zone: 'Kitchen' })
    expect(typeof payload.request_id).toBe('string')
    expect(payload).not.toHaveProperty('position_seconds')
  })

  it('zoneCommand seek floors the position and clamps negatives to 0', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.zoneCommand('Kitchen', 'seek', 42.9)
    expect(lastSent(sendMessage).payload.position_seconds).toBe(42)

    result.current.zoneCommand('Kitchen', 'seek', -5)
    expect(lastSent(sendMessage).payload.position_seconds).toBe(0)
  })

  it('zoneCommand seek treats undefined positionSeconds as 0', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.zoneCommand('Kitchen', 'seek')
    expect(lastSent(sendMessage).payload.position_seconds).toBe(0)
  })

  it('setVolume rounds the volume value before dispatch', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.setVolume('out-1', 42.4)
    expect(lastSent(sendMessage).payload).toMatchObject({
      action: 'set_volume',
      output: 'out-1',
      volume: 42,
    })

    result.current.setVolume('out-1', 42.6)
    expect(lastSent(sendMessage).payload.volume).toBe(43)
  })

  it('mute dispatches the boolean unchanged', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.mute('out-1', true)
    expect(lastSent(sendMessage).payload).toMatchObject({
      action: 'mute',
      output: 'out-1',
      mute: true,
    })

    result.current.mute('out-1', false)
    expect(lastSent(sendMessage).payload.mute).toBe(false)
  })

  it('playFromHere dispatches action with zone + queue_item_id', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.playFromHere('Kitchen', 12345)
    expect(lastSent(sendMessage).payload).toMatchObject({
      action: 'play_from_here',
      zone: 'Kitchen',
      queue_item_id: 12345,
    })
  })

  it('verifyFeature sends a feature-verify-request with the feature name', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.verifyFeature('stop_marker')

    const { channel, payload } = lastSent(sendMessage)
    expect(channel).toBe('feature-verify-request')
    expect(payload.feature).toBe('stop_marker')
    expect(typeof payload.request_id).toBe('string')
    // No action / zone fields — this is a feature check, not a Roon
    // control request, and the agent must not interpret it as one.
    expect(payload).not.toHaveProperty('action')
    expect(payload).not.toHaveProperty('zone')
  })

  it('each dispatch mints a fresh request_id', () => {
    const sendMessage = makeSend()
    const { result } = renderHook(() => useRoonCommands(sendMessage))

    result.current.zoneCommand('Kitchen', 'play')
    result.current.zoneCommand('Kitchen', 'pause')

    const ids = sendMessage.mock.calls.map(
      ([, body]) => (JSON.parse(body) as { request_id: string }).request_id,
    )
    expect(new Set(ids).size).toBe(2)
  })
})
