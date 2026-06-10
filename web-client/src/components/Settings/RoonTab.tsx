/**
 * Roon connection settings.
 *
 * The agent auto-discovers Roon Cores on the network and pairs via
 * the Roon extension authorization flow. These fields are overrides
 * for non-default network topologies (e.g. Core on a different
 * subnet, multiple Cores on the LAN, multi-profile setups).
 */
import React from 'react'
import f from './fields.module.css'
import { TextField } from './fields'
import { useTabForm } from './useTabForm'
import { usePublishTabForm } from './settingsFormContext'
import type { UseSettingsState } from '../../hooks/useSettingsState'

const FIELDS = [
  'DEFAULT_ROON_ZONE',
  'ROON_CORE_URL',
  'ROON_CORE_NAME',
  'ROON_PROFILE_NAME',
] as const

type FieldKey = (typeof FIELDS)[number]

export const RoonTab: React.FC<{ state: UseSettingsState }> = ({ state }) => {
  const form = useTabForm<Record<FieldKey, string>>({
    state,
    fields: FIELDS,
  })
  usePublishTabForm('roon', form.dirty, form.buildUpdates, form.reset)

  return (
    <div>
      <p className={f.tabIntro}>
        Optional overrides for your Roon setup. Leave everything blank
        if auto-discovery is working for you.
      </p>

      <TextField
        id="roon-default-zone"
        label="Default zone"
        value={form.values.DEFAULT_ROON_ZONE}
        onChange={(v) => form.setField('DEFAULT_ROON_ZONE', v)}
        placeholder="e.g. Living Room"
        help="The Roon zone the assistant uses by default. Leave blank to use whichever zone Roon shows first."
      />

      <TextField
        id="roon-core-url"
        label="Roon Core URL"
        value={form.values.ROON_CORE_URL}
        onChange={(v) => form.setField('ROON_CORE_URL', v)}
        placeholder="e.g. http://192.168.1.50:9330"
        type="url"
        monospace
        help="Address of your Roon Core — only needed if auto-discovery can't find it."
      />

      <TextField
        id="roon-core-name"
        label="Roon Core name"
        value={form.values.ROON_CORE_NAME}
        onChange={(v) => form.setField('ROON_CORE_NAME', v)}
        placeholder="e.g. Music Room PC"
        help="If you have several Roon Cores on the network, the name of the one to pair with. Find it in Roon under Settings → General → Name."
      />

      <TextField
        id="roon-profile-name"
        label="Roon profile name"
        value={form.values.ROON_PROFILE_NAME}
        onChange={(v) => form.setField('ROON_PROFILE_NAME', v)}
        placeholder="e.g. Family"
        help="The Roon profile to sign in as. Defaults to the first one available."
      />

    </div>
  )
}
