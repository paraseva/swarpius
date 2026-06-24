export type AppView = 'assistant' | 'analysis' | 'settings' | 'roon-setup' | 'roon-explorer' | 'cost'

/**
 * View to land on after a restart initiated from the Settings page: return to
 * the assistant (don't leave the user parked on Settings once it validates),
 * but leave any other current view unchanged (a restart must not pull the user
 * off the view they were on).
 */
export function viewAfterRestart(current: AppView): AppView {
  return current === 'settings' ? 'assistant' : current
}
