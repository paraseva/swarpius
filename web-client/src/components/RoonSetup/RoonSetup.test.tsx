import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RoonSetup } from './RoonSetup'
import type { UseSettingsState } from '../../hooks/useSettingsState'

afterEach(cleanup)

const failedState = {
  roonState: 'failed',
  roonFailureReason:
    'No Roon Cores found on the network. ... set ROON_CORE_URL=... in agent/.env (required on Docker setups).',
  roonStatusMessage: '',
} as unknown as UseSettingsState

describe('RoonSetup failure view', () => {
  it('leads with Settings → Roon Core URL guidance, not the raw backend message', () => {
    render(<RoonSetup state={failedState} onOpenSettings={() => {}} />)
    expect(screen.getByRole('heading', { name: /Roon setup failed/i })).toBeInTheDocument()
    expect(screen.getByText(/Roon Core URL/i)).toBeInTheDocument()
    expect(screen.queryByText(/Docker/i)).toBeNull()
    expect(screen.queryByText(/agent\/\.env/i)).toBeNull()
  })

  it('fires onOpenSettings from the Open Settings escape hatch', () => {
    const onOpenSettings = vi.fn()
    render(<RoonSetup state={failedState} onOpenSettings={onOpenSettings} />)
    screen.getByRole('button', { name: /open settings/i }).click()
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })
})
