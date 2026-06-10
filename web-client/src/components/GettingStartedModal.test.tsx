import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GettingStartedModal, GETTING_STARTED_ID } from './GettingStartedModal'
import { GuidanceContext } from './guidanceContext'
import type { GuidanceEntry } from '../utils/parseGuidanceSections'

const entry: GuidanceEntry = {
  id: GETTING_STARTED_ID,
  title: 'Getting Started',
  content: 'Welcome to **Swarpius** — add an LLM key to begin.',
  docFile: 'guide',
  devOnly: false,
  bundleOnly: false,
}

const stopEntry: GuidanceEntry = {
  id: 'stop-marker',
  title: 'Enabling the Stop button',
  content: 'Drop the silent track into a Roon-watched folder.',
  docFile: 'guide',
  devOnly: false,
  bundleOnly: true,
}

function renderModal(onClose = vi.fn()) {
  const sections: Record<string, GuidanceEntry> = { [GETTING_STARTED_ID]: entry }
  render(
    <GuidanceContext value={sections}>
      <GettingStartedModal onClose={onClose} />
    </GuidanceContext>,
  )
  return onClose
}

afterEach(cleanup)

describe('GettingStartedModal', () => {
  it('renders the getting-started guidance content', () => {
    renderModal()
    expect(screen.getByText('Getting Started')).toBeInTheDocument()
    // Markdown body is rendered (bold span from the source markdown).
    expect(screen.getByText('Swarpius')).toBeInTheDocument()
  })

  it('fires onClose from both the close icon and the primary button', async () => {
    const onClose = renderModal()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.click(screen.getByRole('button', { name: 'Get started' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('renders nothing when the guidance section is absent', () => {
    const { container } = render(
      <GuidanceContext value={{}}>
        <GettingStartedModal onClose={vi.fn()} />
      </GuidanceContext>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the stop-marker setup and an open-folder button on the bundle', async () => {
    const onOpen = vi.fn()
    const sections: Record<string, GuidanceEntry> = {
      [GETTING_STARTED_ID]: entry,
      'stop-marker': stopEntry,
    }
    render(
      <GuidanceContext value={sections}>
        <GettingStartedModal onClose={vi.fn()} isBundle onOpenStopMarkerFolder={onOpen} />
      </GuidanceContext>,
    )
    expect(screen.getByText('Enabling the Stop button')).toBeInTheDocument()
    const btn = screen.getByRole('button', { name: /open the stop-marker folder/i })
    await userEvent.setup().click(btn)
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('hides the stop-marker setup when not running as a bundle', () => {
    const sections: Record<string, GuidanceEntry> = {
      [GETTING_STARTED_ID]: entry,
      'stop-marker': stopEntry,
    }
    render(
      <GuidanceContext value={sections}>
        <GettingStartedModal onClose={vi.fn()} isBundle={false} onOpenStopMarkerFolder={vi.fn()} />
      </GuidanceContext>,
    )
    expect(screen.queryByText('Enabling the Stop button')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /open the stop-marker folder/i }),
    ).not.toBeInTheDocument()
  })
})
