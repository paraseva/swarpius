---
name: roon-config
description: >
  Manage Roon zone configuration: set/get default zone, manage zone aliases
  (set, remove, rename, list, clear), group/ungroup zones, transfer playback
  between zones.
requires_tool: roon_config
---

## Default zone

- `Set Default Zone` — set the default zone for all operations when no zone is specified. Requires `zone`.
- `Get Default Zone` — returns the current default zone.

## Zone aliases

Zone aliases attach a friendly name to a zone. Aliasing a *grouped* zone isn't supported in this version.

- `Set Zone Alias` — alias a zone. Requires `alias` (the friendly name) and `zone` (the zone to alias). The alias name itself must not match any existing zone name.
- `Remove Zone Alias` — remove an alias by its name.
- `Rename Zone Alias` — rename an alias. Requires `alias` (current name) and `new_name`.
- `Get Zone Aliases` — list all aliases with their zone names.
- `Clear All Zone Aliases` — remove every alias.

## Transfer

- `Transfer Zone` — transfer playback from one zone to another. Requires `zone` (source) and `zone_to_transfer_to` (target). Does *not* change the default zone — use `Set Default Zone` separately if the user also wants to change their default.

## Zone grouping

- `Group Zones` groups two or more zones to play in sync. Requires `group_zones` (list of zone names, minimum 2). The first listed zone becomes the primary (its queue is preserved). Grouped zones display under Roon's default name (e.g. `MDAC+ USB + 1`); they can't be aliased directly.
- `Ungroup Zones` breaks a group. Provide `zone` (the grouped zone's display name like "A + 1", or any member's name, or an alias of a member). All members are ungrouped.
- `Get Groups` lists all currently grouped zones.
- Grouping a zone that's already in another group pulls it (and any other members requested) into the new group.
- Groups created in other Roon clients (desktop app, etc.) are fully visible and manageable.
