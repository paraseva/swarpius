export interface StopButtonTooltipInputs {
  /** WebSocket is open. */
  connected: boolean
  /** The silent stop-marker track is installed in the Roon library. */
  stopMarkerAvailable: boolean
  /** A re-check is in flight (between click and the next availability update). */
  isVerifying: boolean
  /** Running as the desktop bundle, where the Getting Started guide carries
   *  one-click setup for the stop marker. */
  isBundle: boolean
}

/**
 * Tooltip text for a zone card's STOP button. The bundle and source/Docker
 * builds diverge only in the "not set up" message: the bundle points users
 * at the Getting Started guide (which has the folder-opening setup button),
 * while source/Docker keep the established wording unchanged.
 */
export function stopButtonTooltip(i: StopButtonTooltipInputs): string {
  if (!i.connected) return 'Controls unavailable: websocket is not connected'
  if (i.stopMarkerAvailable) return 'Stop (clears queue)'
  if (i.isVerifying) return 'Checking for stop marker…'
  return i.isBundle
    ? 'Stop needs a one-time setup — see Getting Started → Enabling the Stop button. Click to re-check.'
    : 'Stop marker not in Roon library — click to retry (won’t affect playback)'
}
