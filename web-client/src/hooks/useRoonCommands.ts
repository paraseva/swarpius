import { useMemo } from 'react'
import { createUuid } from '../utils/uuid'

type SendMessage = (channel: string, body: string) => string

/**
 * Collapses the 4 near-identical roon-control-request senders
 * (play/pause/seek/prev/next, volume, mute, play_from_here) into a
 * single object, each returning the minted request id in case the
 * caller wants to correlate responses.
 */
export function useRoonCommands(sendMessage: SendMessage) {
  return useMemo(() => {
    const dispatch = (payload: Record<string, unknown>): string => {
      const requestId = createUuid()
      sendMessage('roon-control-request', JSON.stringify({ request_id: requestId, ...payload }))
      return requestId
    }

    return {
      zoneCommand: (
        zoneDisplayName: string,
        command: 'play' | 'pause' | 'stop' | 'next' | 'previous' | 'seek',
        positionSeconds?: number,
      ) => {
        const payload: Record<string, unknown> = { action: command, zone: zoneDisplayName }
        if (command === 'seek') {
          payload.position_seconds = Math.max(0, Math.floor(positionSeconds ?? 0))
        }
        return dispatch(payload)
      },
      setVolume: (outputName: string, volume: number) =>
        dispatch({ action: 'set_volume', output: outputName, volume: Math.round(volume) }),
      mute: (outputName: string, mute: boolean) =>
        dispatch({ action: 'mute', output: outputName, mute }),
      playFromHere: (zoneDisplayName: string, queueItemId: number) =>
        dispatch({ action: 'play_from_here', zone: zoneDisplayName, queue_item_id: queueItemId }),
      // User-triggered re-check of a feature's availability. Currently
      // only "stop_marker" is wired up: sent by the waiting-state stop
      // button to ask the agent to re-run StopMarkerCoordinator.initialise()
      // without affecting playback. Result lands on feature-availability.
      verifyFeature: (feature: 'stop_marker') => {
        const requestId = createUuid()
        sendMessage('feature-verify-request', JSON.stringify({ request_id: requestId, feature }))
        return requestId
      },
      // Bundle-only: ask the agent to open the stop-marker folder in the
      // OS file manager (agent and browser share a machine). Fire-and-
      // forget; the agent refuses outside a desktop bundle.
      openStopMarkerFolder: () => {
        const requestId = createUuid()
        sendMessage('open-data-folder-request', JSON.stringify({ request_id: requestId }))
        return requestId
      },
    }
  }, [sendMessage])
}

export type RoonCommands = ReturnType<typeof useRoonCommands>
