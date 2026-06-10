import { describe, expect, it } from 'vitest'
import { buildDropdownEntries } from './defaultZoneDropdown'
import type { DefaultZoneInfo } from './DefaultZoneBadge'

type ZoneOption = {
  display_name: string
  zone_alias: string | null
  group_name: string | null
  state: string
  is_default: boolean
  is_grouped: boolean
  group_members: string[]
}

const zone = (display_name: string, overrides: Partial<ZoneOption> = {}): ZoneOption => ({
  display_name,
  zone_alias: null,
  group_name: null,
  state: 'stopped',
  is_default: false,
  is_grouped: false,
  group_members: [display_name],
  ...overrides,
})

describe('buildDropdownEntries', () => {
  it('returns the fetched zones unchanged when defaultZone is null', () => {
    const zones = [zone('Speakers'), zone('Kitchen')]
    expect(buildDropdownEntries(null, zones)).toEqual(zones)
  })

  it('returns the fetched zones unchanged when default is online and present in the list', () => {
    const fetched = [
      zone('Speakers', { is_default: true }),
      zone('Kitchen'),
    ]
    const defaultZone: DefaultZoneInfo = {
      zone_name: 'Speakers',
      alias: null,
      group_name: null,
      is_grouped: false,
      is_online: true,
    }
    expect(buildDropdownEntries(defaultZone, fetched)).toEqual(fetched)
  })

  it('prepends the offline default to the list when it is not in the fetch response', () => {
    const fetched = [zone('Speakers'), zone('Kitchen')]
    const defaultZone: DefaultZoneInfo = {
      zone_name: 'Headphones',
      alias: 'BT',
      group_name: null,
      is_grouped: false,
      is_online: false,
    }
    const result = buildDropdownEntries(defaultZone, fetched)

    expect(result).toHaveLength(3)
    expect(result[0].display_name).toBe('Headphones')
    expect(result[0].is_default).toBe(true)
    expect(result[0].state).toBe('offline')
    expect(result[0].zone_alias).toBe('BT')
    expect(result.slice(1)).toEqual(fetched)
  })

  it('does not duplicate the default if it somehow appears in the fetch list and is offline', () => {
    // Defensive: fetched list shouldn't include offline zones, but if it
    // ever did we mustn't render the same zone twice.
    const fetched = [zone('Headphones'), zone('Kitchen')]
    const defaultZone: DefaultZoneInfo = {
      zone_name: 'Headphones',
      alias: null,
      group_name: null,
      is_grouped: false,
      is_online: false,
    }
    const result = buildDropdownEntries(defaultZone, fetched)
    expect(result).toHaveLength(2)
    expect(result.filter((z) => z.display_name === 'Headphones')).toHaveLength(1)
  })

  it('passes group_name through for an offline grouped default', () => {
    const fetched = [zone('Speakers')]
    const defaultZone: DefaultZoneInfo = {
      zone_name: 'BT-W5 Akash',
      alias: 'Headphones',
      group_name: 'Upstairs',
      is_grouped: true,
      is_online: false,
    }
    const result = buildDropdownEntries(defaultZone, fetched)

    expect(result[0].group_name).toBe('Upstairs')
    expect(result[0].is_grouped).toBe(true)
  })
})

describe('isDefaultOffline', () => {
  it('is true only when zone_name set and is_online is false', async () => {
    const { isDefaultOffline } = await import('./defaultZoneDropdown')
    expect(isDefaultOffline(null)).toBe(false)
    expect(isDefaultOffline({
      zone_name: null, alias: null, group_name: null, is_grouped: false, is_online: false,
    })).toBe(false)
    expect(isDefaultOffline({
      zone_name: 'A', alias: null, group_name: null, is_grouped: false, is_online: true,
    })).toBe(false)
    expect(isDefaultOffline({
      zone_name: 'A', alias: null, group_name: null, is_grouped: false, is_online: false,
    })).toBe(true)
  })
})
