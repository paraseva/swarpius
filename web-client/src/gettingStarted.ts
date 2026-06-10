export interface AutoShowWelcomeInputs {
  /** Agent flag: no assistant-configuration value has been set. Durable
   *  across restarts, so it gates the once-per-fresh-install intro. */
  configPristine: boolean
  /** No feature-availability message has arrived yet — defer the
   *  decision so we don't flash the intro during the WS handshake. */
  awaitingFirstUpdate: boolean
  /** The intro has already opened this session (latch), so dismissing it
   *  while still unconfigured doesn't immediately re-open it. */
  alreadyShown: boolean
}

/**
 * Whether to auto-open the Getting Started intro on this render. Shows
 * once per session on a pristine install; the user setting anything
 * (which clears `configPristine`) retires it for good.
 */
export function shouldAutoShowWelcome(i: AutoShowWelcomeInputs): boolean {
  return i.configPristine && !i.awaitingFirstUpdate && !i.alreadyShown
}
