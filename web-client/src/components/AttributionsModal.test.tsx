import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AttributionsModal } from './AttributionsModal'

const sample = {
  generated_by: 'licensing/licenses.py',
  scope: 'web-client production dependencies (served to the browser)',
  components: [
    { name: 'react', version: '19.2.6', license: 'MIT', url: 'https://www.npmjs.com/package/react' },
    { name: '@types/estree', version: '1.0.8', license: 'MIT', url: '' },
  ],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, json: async () => sample }),
  )
})

describe('AttributionsModal', () => {
  it('fetches licenses.json and lists each dependency with its version and licence', async () => {
    render(<AttributionsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('react')).toBeInTheDocument())
    expect(screen.getByText('@types/estree')).toBeInTheDocument()
    expect(screen.getByText(/19\.2\.6/)).toBeInTheDocument()
    expect(screen.getAllByText('MIT').length).toBeGreaterThanOrEqual(2)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('licenses.json'))
  })

  it('invokes onClose from the close control', async () => {
    const onClose = vi.fn()
    render(<AttributionsModal onClose={onClose} />)
    await waitFor(() => expect(screen.getByText('react')).toBeInTheDocument())
    screen.getByRole('button', { name: /close/i }).click()
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
