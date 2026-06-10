import type { DefaultZoneInfo } from './DefaultZoneBadge'

export interface ZoneOption {
  display_name: string
  zone_alias: string | null
  group_name: string | null
  state: string
  is_default: boolean
  is_grouped: boolean
  group_members: string[]
}

export const isDefaultOffline = (zone: DefaultZoneInfo | null): boolean =>
  Boolean(zone && zone.zone_name && zone.is_online === false)

/**
 * Build the list of zones the dropdown should render.
 *
 * The agent's `list_zones` response only includes zones currently
 * visible to Roon — an offline default zone (e.g. BT headphones in
 * standby) won't appear there. We synthesise an entry for it so the
 * user can see what their default *is*, marked as offline, and choose
 * to switch away from it. The synthetic entry is dropped automatically
 * once the default is online again or the user picks a different one
 * (the new default goes through the normal flow).
 */
export const buildDropdownEntries = (
  defaultZone: DefaultZoneInfo | null,
  fetchedZones: ZoneOption[],
): ZoneOption[] => {
  if (!isDefaultOffline(defaultZone) || !defaultZone?.zone_name) {
    return fetchedZones
  }
  const name = defaultZone.zone_name
  const alreadyPresent = fetchedZones.some(
    (z) => z.display_name.toLowerCase() === name.toLowerCase(),
  )
  if (alreadyPresent) {
    return fetchedZones
  }
  const synthetic: ZoneOption = {
    display_name: name,
    zone_alias: defaultZone.alias,
    group_name: defaultZone.group_name,
    state: 'offline',
    is_default: true,
    is_grouped: defaultZone.is_grouped,
    group_members: [name],
  }
  return [synthetic, ...fetchedZones]
}
