import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SelectField } from './fields'

afterEach(cleanup)

const options = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
]

const option = (name: string) =>
  screen.getByRole('option', { name }) as HTMLOptionElement

describe('SelectField placeholder', () => {
  it('shows the placeholder (value "") as selected when the value is empty', () => {
    render(
      <SelectField
        id="p" label="Provider" value="" onChange={() => {}}
        options={options} placeholder="Select provider…"
      />,
    )
    const ph = option('Select provider…')
    expect(ph.value).toBe('')
    expect(ph.selected).toBe(true)
    // Regression: the first real option must NOT be silently selected
    // when nothing is chosen (that mismatch made canTest see an empty
    // provider while the dropdown displayed one).
    expect(option('Anthropic').selected).toBe(false)
  })

  it('selects the matching option when a value is set, not the placeholder', () => {
    render(
      <SelectField
        id="p" label="Provider" value="anthropic" onChange={() => {}}
        options={options} placeholder="Select provider…"
      />,
    )
    expect(option('Anthropic').selected).toBe(true)
    expect(option('Select provider…').selected).toBe(false)
  })
})
