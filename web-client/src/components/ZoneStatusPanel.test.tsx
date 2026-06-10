import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  WebSocketContext,
  type SocketMessage,
  type WebSocketContextValue,
} from '../websocketContext'
import { ZoneStatusPanel } from './ZoneStatusPanel'

let msgId = 0

const makeMsg = (
  channel: string,
  payload: Record<string, unknown>,
  timestamp = Date.now(),
): SocketMessage => ({
  id: `m-${++msgId}`,
  channel,
  direction: 'inbound',
  body: JSON.stringify(payload),
  payload,
  timestamp,
})

/** Build a zone-snapshots WS message carrying the given zones. */
const makeSnapshot = (zones: Record<string, unknown>[]) =>
  makeMsg('zone-snapshots', {
    source: '[Roon snapshot]',
    data: {
      zones,
      timestamp_ms: Date.now(),
    },
  })

const playingZone = (id = 'zone-1', name = 'Living Room') => ({
  zone_id: id,
  display_name: name,
  zone_alias: null,
  group_name: null,
  state: 'playing',
  seek_position: 45,
  is_grouped: false,
  group_members: [name],
  outputs_volume: [],
  image_key: null,
  now_playing: { line1: 'Track Title', line2: 'Artist', line3: 'Album', length: 300 },
})

const pausedZone = (id = 'zone-1', name = 'Living Room') => ({
  ...playingZone(id, name),
  state: 'paused',
})

const stoppedZone = (id = 'zone-1', name = 'Living Room') => ({
  ...playingZone(id, name),
  state: 'stopped',
  seek_position: 0,
  now_playing: { line1: null, line2: null, line3: null, length: null },
})

const renderPanel = (
  events: SocketMessage[],
  sendMessage: WebSocketContextValue['sendMessage'] = () => '',
) => {
  // Replays the event list as one rerender per snapshot — non-snapshot
  // messages ride along in the next stage so the panel's marker-title
  // pre-scan still sees them in the same batch as the next snapshot.
  type Stage = { snapshot: unknown; appendedMessages: SocketMessage[] }
  const stages: Stage[] = []
  let pendingMessages: SocketMessage[] = []
  let lastSnapshot: unknown = null
  for (const m of events) {
    if (m.channel === 'zone-snapshots') {
      stages.push({ snapshot: m.payload, appendedMessages: pendingMessages })
      lastSnapshot = m.payload
      pendingMessages = []
    } else {
      pendingMessages.push(m)
    }
  }
  if (pendingMessages.length > 0 || stages.length === 0) {
    stages.push({ snapshot: lastSnapshot, appendedMessages: pendingMessages })
  }

  let messages: SocketMessage[] = []
  let latestZoneSnapshot: unknown = null
  const ctxFor = (): WebSocketContextValue => ({
    status: 'open',
    messages,
    sendMessage,
    isLlmActive: false,
    latestZoneSnapshot,
    connectionGeneration: 0,
    isRestarting: false,
    markRestarting: () => {},
    trimmedCount: 0,
  })

  const result = render(
    <WebSocketContext.Provider value={ctxFor()}>
      <ZoneStatusPanel />
    </WebSocketContext.Provider>,
  )

  for (const stage of stages) {
    messages = [...messages, ...stage.appendedMessages]
    latestZoneSnapshot = stage.snapshot
    result.rerender(
      <WebSocketContext.Provider value={ctxFor()}>
        <ZoneStatusPanel />
      </WebSocketContext.Provider>,
    )
  }

  return result
}

describe('ZoneStatusPanel — zone card lifecycle', () => {
  afterEach(() => {
    cleanup()
    msgId = 0
  })

  it('creates a card when the snapshot reports a playing zone', () => {
    renderPanel([makeSnapshot([playingZone()])])

    expect(screen.getByText('Living Room')).toBeInTheDocument()
    expect(screen.getByText('PLAYING')).toBeInTheDocument()
  })

  it('creates a card for a paused zone', () => {
    renderPanel([makeSnapshot([pausedZone()])])

    expect(screen.getByText('Living Room')).toBeInTheDocument()
    expect(screen.getByText('PAUSED')).toBeInTheDocument()
  })

  it('does not display a stopped zone that arrives stopped in the first snapshot', () => {
    renderPanel([makeSnapshot([stoppedZone()])])

    expect(screen.queryByText('Living Room')).toBeNull()
    expect(screen.getByText('Nothing playing')).toBeInTheDocument()
  })

  it('keeps a transitioning playing→stopped zone visible for ~2 seconds, then drops it', () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    try {
      renderPanel([
        makeSnapshot([playingZone()]),
        makeSnapshot([stoppedZone()]),
      ])

      expect(screen.getByText('Living Room')).toBeInTheDocument()
      expect(screen.getByText('STOPPED')).toBeInTheDocument()

      act(() => {
        vi.advanceTimersByTime(3000)
      })
      expect(screen.queryByText('Living Room')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps a transitioning paused→stopped zone visible for ~2 seconds, then drops it', () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    try {
      renderPanel([
        makeSnapshot([pausedZone()]),
        makeSnapshot([stoppedZone()]),
      ])

      expect(screen.getByText('Living Room')).toBeInTheDocument()
      expect(screen.getByText('STOPPED')).toBeInTheDocument()

      act(() => {
        vi.advanceTimersByTime(3000)
      })
      expect(screen.queryByText('Living Room')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('renders multiple zones from a single snapshot', () => {
    renderPanel([
      makeSnapshot([
        playingZone('zone-1', 'Living Room'),
        playingZone('zone-2', 'Kitchen'),
      ]),
    ])

    expect(screen.getByText('Living Room')).toBeInTheDocument()
    expect(screen.getByText('Kitchen')).toBeInTheDocument()
    expect(screen.getByText('2 zones')).toBeInTheDocument()
  })

  it('updates state across successive snapshots', () => {
    renderPanel([
      makeSnapshot([playingZone()]),
      makeSnapshot([pausedZone()]),
    ])

    expect(screen.getByText('Living Room')).toBeInTheDocument()
    expect(screen.getByText('PAUSED')).toBeInTheDocument()
  })

  it('removes a card when the zone disappears from the next snapshot', () => {
    renderPanel([
      makeSnapshot([playingZone('zone-1', 'Living Room')]),
      makeSnapshot([]),
    ])

    expect(screen.queryByText('Living Room')).toBeNull()
    expect(screen.getByText('Nothing playing')).toBeInTheDocument()
  })

  it('renders a card with alias from the snapshot', () => {
    renderPanel([
      makeSnapshot([{ ...playingZone(), zone_alias: 'Lounge' }]),
    ])

    expect(screen.getByText('Lounge (Living Room)')).toBeInTheDocument()
    expect(screen.getByText('PLAYING')).toBeInTheDocument()
  })

  it('renders a card with group name from the snapshot', () => {
    renderPanel([
      makeSnapshot([{
        ...playingZone('zone-1', 'MDAC+ USB + 1'),
        is_grouped: true,
        group_members: ['MDAC+ USB', 'Rotel'],
        group_name: 'Whole House',
      }]),
    ])

    expect(screen.getByText('Whole House (MDAC+ USB + 1)')).toBeInTheDocument()
  })

  // ── Volume slider toggle ───────────────────────────────────────

  const livingRoomWithVolume = (volume: number | null = 50, type: string | null = 'db') => ({
    ...playingZone(),
    outputs_volume: [
      { name: 'Living Room', value: volume, type, is_muted: false, min: 0, max: 100, step: 1 },
    ],
  })

  it('volume slider is hidden by default, shows value as toggle', () => {
    renderPanel([makeSnapshot([livingRoomWithVolume()])])

    expect(screen.getByRole('button', { name: 'Show volume slider' })).toBeInTheDocument()
    expect(screen.queryByRole('slider', { name: /Volume/ })).toBeNull()
  })

  it('clicking volume toggle reveals the slider', async () => {
    const user = userEvent.setup()
    renderPanel([makeSnapshot([livingRoomWithVolume()])])

    await user.click(screen.getByRole('button', { name: 'Show volume slider' }))

    expect(screen.getByRole('slider', { name: /Volume/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide volume slider' })).toBeInTheDocument()
  })

  it('clicking volume toggle again hides the slider', async () => {
    const user = userEvent.setup()
    renderPanel([makeSnapshot([livingRoomWithVolume()])])

    await user.click(screen.getByRole('button', { name: 'Show volume slider' }))
    expect(screen.getByRole('slider', { name: /Volume/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Hide volume slider' }))
    expect(screen.queryByRole('slider', { name: /Volume/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Show volume slider' })).toBeInTheDocument()
  })

  it('fixed volume outputs do not show a toggle', () => {
    renderPanel([makeSnapshot([livingRoomWithVolume(null, null)])])

    expect(screen.getByText('Fixed volume')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /volume slider/ })).toBeNull()
  })

  it('clicking outside the volume row collapses the slider', async () => {
    const user = userEvent.setup()
    renderPanel([makeSnapshot([livingRoomWithVolume()])])

    await user.click(screen.getByRole('button', { name: 'Show volume slider' }))
    expect(screen.getByRole('slider', { name: /Volume/ })).toBeInTheDocument()

    await user.click(screen.getByText('Living Room'))
    expect(screen.queryByRole('slider', { name: /Volume/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Show volume slider' })).toBeInTheDocument()
  })
})

describe('ZoneStatusPanel — volume modal (multi-output)', () => {
  afterEach(() => {
    cleanup()
    msgId = 0
  })

  const multiOutputSnapshot = (outputsVolume: Record<string, unknown>[]) =>
    makeSnapshot([{
      ...playingZone('zone-1', 'Whole House'),
      is_grouped: true,
      group_members: ['Living Room', 'Kitchen'],
      outputs_volume: outputsVolume,
    }])

  it('renders a "Volume (N outputs)" launcher and opens the modal on click', async () => {
    const user = userEvent.setup()
    renderPanel([
      multiOutputSnapshot([
        { name: 'Living Room', value: 50, type: 'db', is_muted: false, min: 0, max: 100, step: 1 },
        { name: 'Kitchen',     value: 30, type: 'db', is_muted: false, min: 0, max: 100, step: 1 },
      ]),
    ])

    const launcher = screen.getByRole('button', { name: /Volume \(2 outputs\)/ })
    expect(launcher).toBeInTheDocument()

    await user.click(launcher)

    expect(screen.getByRole('slider', { name: /Volume, Living Room/ })).toBeInTheDocument()
    expect(screen.getByRole('slider', { name: /Volume, Kitchen/ })).toBeInTheDocument()
  })

  it('clicking an output mute button dispatches a mute roon-control-request for that output', async () => {
    const sendMessage = vi.fn<WebSocketContextValue['sendMessage']>(() => 'm-1')
    const user = userEvent.setup()
    renderPanel(
      [
        multiOutputSnapshot([
          { name: 'Living Room', value: 50, type: 'db', is_muted: false, min: 0, max: 100, step: 1 },
          { name: 'Kitchen',     value: 30, type: 'db', is_muted: false, min: 0, max: 100, step: 1 },
        ]),
      ],
      sendMessage,
    )

    await user.click(screen.getByRole('button', { name: /Volume \(2 outputs\)/ }))
    await user.click(screen.getByRole('button', { name: 'Mute Kitchen' }))

    const muteCalls = sendMessage.mock.calls.filter(([ch]) => ch === 'roon-control-request')
    const muteKitchen = muteCalls
      .map(([, body]) => JSON.parse(body as string))
      .find((p) => p.action === 'mute' && p.output === 'Kitchen')
    expect(muteKitchen).toBeDefined()
    expect(muteKitchen.mute).toBe(true)
  })

  it('fixed-only multi-output groups show "Fixed volume" instead of the modal launcher', () => {
    renderPanel([
      multiOutputSnapshot([
        { name: 'Living Room', value: null, type: null, is_muted: false, min: 0, max: 100, step: 1 },
        { name: 'Kitchen',     value: null, type: null, is_muted: false, min: 0, max: 100, step: 1 },
      ]),
    ])

    expect(screen.getByText('Fixed volume')).toBeInTheDocument()
    expect(screen.queryByText(/Volume \(2 outputs\)/)).toBeNull()
  })
})

describe('ZoneStatusPanel — queue modal', () => {
  afterEach(() => {
    cleanup()
    msgId = 0
  })

  const makeQueueUpdate = (zoneId: string, items: Record<string, unknown>[]) =>
    makeMsg('queue-updates', {
      zone_id: zoneId,
      zone_display_name: 'Living Room',
      items,
    })

  const queueTrack = (id: number, title: string, artist: string, length = 200) => ({
    queue_item_id: id,
    length,
    image_key: null,
    two_line: { line1: title, line2: artist },
  })

  it('queue button is disabled when no queue items are known', () => {
    renderPanel([makeSnapshot([playingZone()])])

    expect(screen.getByRole('button', { name: 'Queue' })).toBeDisabled()
  })

  it('clicking the queue button opens a modal listing tracks', async () => {
    const user = userEvent.setup()
    renderPanel([
      makeSnapshot([playingZone()]),
      makeQueueUpdate('zone-1', [
        queueTrack(1001, 'Now Playing Track', 'Artist A'),
        queueTrack(1002, 'Up Next', 'Artist B'),
        queueTrack(1003, 'Third Track', 'Artist C'),
      ]),
    ])

    await user.click(screen.getByRole('button', { name: 'Queue' }))

    expect(screen.getByText(/Queue — Living Room \(3 tracks/)).toBeInTheDocument()
    expect(screen.getByText('Now Playing Track')).toBeInTheDocument()
    expect(screen.getByText('Up Next')).toBeInTheDocument()
    expect(screen.getByText('Third Track')).toBeInTheDocument()
  })

  it('clicking "Play from here" dispatches play_from_here with the expected queue_item_id', async () => {
    const sendMessage = vi.fn<WebSocketContextValue['sendMessage']>(() => 'm-1')
    const user = userEvent.setup()
    renderPanel(
      [
        makeSnapshot([playingZone()]),
        makeQueueUpdate('zone-1', [
          queueTrack(1001, 'Now Playing Track', 'Artist A'),
          queueTrack(1002, 'Up Next', 'Artist B'),
        ]),
      ],
      sendMessage,
    )

    await user.click(screen.getByRole('button', { name: 'Queue' }))
    await user.click(screen.getByRole('button', { name: 'Play from Up Next' }))

    const controlCalls = sendMessage.mock.calls.filter(([ch]) => ch === 'roon-control-request')
    const playFromHere = controlCalls
      .map(([, body]) => JSON.parse(body as string))
      .find((p) => p.action === 'play_from_here')
    expect(playFromHere).toBeDefined()
    expect(playFromHere.zone).toBe('Living Room')
    expect(playFromHere.queue_item_id).toBe(1002)
  })
})

describe('ZoneStatusPanel — three-state stop button', () => {
  afterEach(() => {
    cleanup()
    msgId = 0
  })

  const featureAvailability = (
    payload: { stop_marker_available?: boolean; simulated_stop_disabled?: boolean },
  ) => makeMsg('feature-availability', payload)

  it('renders a normal stop button by default (optimistic available state)', () => {
    renderPanel([makeSnapshot([playingZone()])])
    const stop = screen.getByLabelText('Stop')
    expect(stop).toBeInTheDocument()
    expect(stop).not.toBeDisabled()
  })

  it('renders a waiting-state stop button (verify aria-label) when marker unavailable', () => {
    renderPanel([
      makeSnapshot([playingZone()]),
      featureAvailability({ stop_marker_available: false }),
    ])
    expect(screen.queryByLabelText('Stop')).toBeNull()
    const verify = screen.getByLabelText('Verify stop marker availability')
    expect(verify).toBeInTheDocument()
    expect(verify).not.toBeDisabled()
  })

  it('hides the stop button entirely when simulated_stop_disabled', () => {
    renderPanel([
      makeSnapshot([playingZone()]),
      featureAvailability({ simulated_stop_disabled: true, stop_marker_available: false }),
    ])
    expect(screen.queryByLabelText('Stop')).toBeNull()
    expect(screen.queryByLabelText('Verify stop marker availability')).toBeNull()
  })

  it('available-state click sends roon-control-request action=stop', async () => {
    const sendMessage = vi.fn<(channel: string, body: string) => string>(() => '')
    const user = userEvent.setup()
    renderPanel(
      [
        makeSnapshot([playingZone()]),
        featureAvailability({ stop_marker_available: true }),
      ],
      sendMessage,
    )
    await user.click(screen.getByLabelText('Stop'))

    const calls = sendMessage.mock.calls
    expect(calls.length).toBeGreaterThan(0)
    const last = calls[calls.length - 1]!
    expect(last[0]).toBe('roon-control-request')
    const payload = JSON.parse(last[1]) as Record<string, unknown>
    expect(payload.action).toBe('stop')
    expect(payload.zone).toBe('Living Room')
  })

  it('waiting-state click immediately switches the button to a verifying affordance', async () => {
    // Without the in-flight indicator the click is visually silent
    // until (and unless) the broadcast flips state. Users would think
    // the button is broken when the marker is still missing.
    const sendMessage = vi.fn<(channel: string, body: string) => string>(() => '')
    const user = userEvent.setup()
    renderPanel(
      [
        makeSnapshot([playingZone()]),
        featureAvailability({ stop_marker_available: false }),
      ],
      sendMessage,
    )
    const verify = screen.getByLabelText('Verify stop marker availability')

    await user.click(verify)

    expect(screen.getByLabelText('Checking for stop marker')).toBeInTheDocument()
    expect(screen.queryByLabelText('Verify stop marker availability')).toBeNull()
    expect(screen.getByLabelText('Checking for stop marker')).toBeDisabled()
  })

  it('a feature-availability broadcast clears the verifying indicator', async () => {
    // The agent's verify handler always emits a broadcast (even when
    // the marker is still missing) so the in-flight indicator clears
    // deterministically — independent of whether stop_marker_available
    // actually changed.
    const sendMessage = vi.fn<(channel: string, body: string) => string>(() => '')
    const user = userEvent.setup()
    const ctx: WebSocketContextValue = {
      status: 'open',
      messages: [] as SocketMessage[],
      sendMessage,
      isLlmActive: false,
      latestZoneSnapshot: null as unknown,
      connectionGeneration: 0,
      isRestarting: false,
      markRestarting: () => {},
      trimmedCount: 0,
    }
    const { rerender } = render(
      <WebSocketContext.Provider value={ctx}>
        <ZoneStatusPanel />
      </WebSocketContext.Provider>,
    )
    const playingSnapshot = makeSnapshot([playingZone()])
    ctx.latestZoneSnapshot = playingSnapshot.payload
    ctx.messages = [featureAvailability({ stop_marker_available: false })]
    rerender(
      <WebSocketContext.Provider value={ctx}>
        <ZoneStatusPanel />
      </WebSocketContext.Provider>,
    )

    await user.click(screen.getByLabelText('Verify stop marker availability'))
    expect(screen.getByLabelText('Checking for stop marker')).toBeInTheDocument()

    ctx.messages = [
      ...ctx.messages,
      featureAvailability({ stop_marker_available: false }),
    ]
    rerender(
      <WebSocketContext.Provider value={ctx}>
        <ZoneStatusPanel />
      </WebSocketContext.Provider>,
    )

    expect(screen.queryByLabelText('Checking for stop marker')).toBeNull()
    expect(screen.getByLabelText('Verify stop marker availability')).toBeInTheDocument()
  })

  it('waiting-state click sends feature-verify-request, NOT roon-control-request', async () => {
    const sendMessage = vi.fn<(channel: string, body: string) => string>(() => '')
    const user = userEvent.setup()
    renderPanel(
      [
        makeSnapshot([playingZone()]),
        featureAvailability({ stop_marker_available: false }),
      ],
      sendMessage,
    )
    await user.click(screen.getByLabelText('Verify stop marker availability'))

    const verifyCall = sendMessage.mock.calls.find(([ch]) => ch === 'feature-verify-request')
    expect(verifyCall).toBeDefined()
    const payload = JSON.parse(verifyCall![1]) as Record<string, unknown>
    expect(payload.feature).toBe('stop_marker')
    const stopCall = sendMessage.mock.calls.find(
      ([ch, body]) => ch === 'roon-control-request'
        && (JSON.parse(body) as { action?: string }).action === 'stop',
    )
    expect(stopCall).toBeUndefined()
  })
})

describe('ZoneStatusPanel — playing liveness indicator', () => {
  afterEach(() => {
    cleanup()
    msgId = 0
  })

  it('renders the indicator when a zone is playing', () => {
    renderPanel([makeSnapshot([playingZone()])])
    expect(screen.getByTestId('zone-playing-indicator')).toBeInTheDocument()
  })

  it('does not render the indicator when a zone is paused', () => {
    renderPanel([makeSnapshot([pausedZone()])])
    expect(screen.queryByTestId('zone-playing-indicator')).toBeNull()
  })

  it('removes the indicator when a playing zone transitions to stopped', () => {
    renderPanel([
      makeSnapshot([playingZone()]),
      makeSnapshot([stoppedZone()]),
    ])
    expect(screen.queryByTestId('zone-playing-indicator')).toBeNull()
  })
})

describe('ZoneStatusPanel — silent stop marker', () => {
  afterEach(() => {
    cleanup()
    msgId = 0
  })

  const markerTitle = 'Swarpius_Stop_Playback'

  const featureAvailability = (payload: { stop_marker_title?: string }) =>
    makeMsg('feature-availability', payload)

  const markerSnapshotZone = () => ({
    ...playingZone(),
    state: 'playing',
    image_key: 'marker-img',
    now_playing: { line1: markerTitle, line2: '', line3: '', length: 1 },
  })

  it('renders state=STOPPED when the marker arrives on top of a playing track', () => {
    renderPanel([
      featureAvailability({ stop_marker_title: markerTitle }),
      makeSnapshot([playingZone()]),
      makeSnapshot([markerSnapshotZone()]),
    ])
    expect(screen.getByText('STOPPED')).toBeInTheDocument()
    expect(screen.queryByText('PLAYING')).toBeNull()
  })

  it('preserves the previously-playing track metadata when the marker arrives', () => {
    // Mirrors Roon's natural end-of-playback behaviour: state flips
    // to stopped but title/album/artist stay visible.
    renderPanel([
      featureAvailability({ stop_marker_title: markerTitle }),
      makeSnapshot([playingZone()]),  // line1='Track Title', line2='Artist', line3='Album'
      makeSnapshot([markerSnapshotZone()]),
    ])
    expect(screen.getByText('Track Title')).toBeInTheDocument()
    expect(screen.getByText('Artist')).toBeInTheDocument()
    expect(screen.getByText('Album')).toBeInTheDocument()
    expect(screen.queryByText(markerTitle)).toBeNull()
  })

  it('honours the marker title from a feature-availability message in the same batch', () => {
    // Pre-scan picks up the title before any same-batch snapshot is
    // evaluated — setState wouldn't propagate until next render.
    renderPanel([
      makeSnapshot([playingZone()]),
      featureAvailability({ stop_marker_title: markerTitle }),
      makeSnapshot([markerSnapshotZone()]),
    ])
    expect(screen.getByText('STOPPED')).toBeInTheDocument()
  })

  it('does not show the card at all when a marker zone arrives on initial connect', () => {
    // Roon can leave api.zones stuck on the marker after stop. With
    // no prior FE state, the filter pins stoppedSince=0 so the card
    // is hidden immediately — NOT a 2-second STOPPED card.
    renderPanel([
      featureAvailability({ stop_marker_title: markerTitle }),
      makeSnapshot([markerSnapshotZone()]),
    ])
    expect(screen.queryByText('Living Room')).toBeNull()
    expect(screen.queryByText('STOPPED')).toBeNull()
    expect(screen.getByText('Nothing playing')).toBeInTheDocument()
  })

  it('does not filter when the marker title is unconfigured', () => {
    renderPanel([
      makeSnapshot([playingZone()]),
      makeSnapshot([markerSnapshotZone()]),
    ])
    expect(screen.getByText('PLAYING')).toBeInTheDocument()
    expect(screen.getByText(markerTitle)).toBeInTheDocument()
  })
})

describe('ZoneStatusPanel — playback setting indicators', () => {
  afterEach(() => {
    cleanup()
    msgId = 0
  })

  it('shows the shuffle icon only when shuffle is on', () => {
    renderPanel([makeSnapshot([{ ...playingZone(), shuffle: true }])])
    expect(screen.getByLabelText('Shuffle on')).toBeInTheDocument()
  })

  it('hides the shuffle icon when shuffle is off', () => {
    renderPanel([makeSnapshot([{ ...playingZone(), shuffle: false }])])
    expect(screen.queryByLabelText('Shuffle on')).toBeNull()
  })

  it('shows "Repeat all" for loop and "Repeat one" for loop_one', () => {
    renderPanel([makeSnapshot([{ ...playingZone(), loop: 'loop' }])])
    expect(screen.getByLabelText('Repeat all')).toBeInTheDocument()
    expect(screen.queryByLabelText('Repeat one')).toBeNull()
  })

  it('shows the repeat-one variant for loop_one', () => {
    renderPanel([makeSnapshot([{ ...playingZone(), loop: 'loop_one' }])])
    expect(screen.getByLabelText('Repeat one')).toBeInTheDocument()
    expect(screen.queryByLabelText('Repeat all')).toBeNull()
  })

  it('hides repeat icons when loop is disabled', () => {
    renderPanel([makeSnapshot([{ ...playingZone(), loop: 'disabled' }])])
    expect(screen.queryByLabelText('Repeat all')).toBeNull()
    expect(screen.queryByLabelText('Repeat one')).toBeNull()
  })

  it('shows the Roon Radio icon only when auto_radio is on', () => {
    renderPanel([makeSnapshot([{ ...playingZone(), auto_radio: true }])])
    expect(screen.getByLabelText('Roon Radio on')).toBeInTheDocument()
  })

  it('hides all indicators when no settings are on', () => {
    renderPanel([makeSnapshot([playingZone()])])
    expect(screen.queryByLabelText('Shuffle on')).toBeNull()
    expect(screen.queryByLabelText('Repeat all')).toBeNull()
    expect(screen.queryByLabelText('Repeat one')).toBeNull()
    expect(screen.queryByLabelText('Roon Radio on')).toBeNull()
  })

  it('updates indicators live across snapshots', () => {
    renderPanel([
      makeSnapshot([{ ...playingZone(), shuffle: false }]),
      makeSnapshot([{ ...playingZone(), shuffle: true, auto_radio: true }]),
    ])
    expect(screen.getByLabelText('Shuffle on')).toBeInTheDocument()
    expect(screen.getByLabelText('Roon Radio on')).toBeInTheDocument()
  })
})
