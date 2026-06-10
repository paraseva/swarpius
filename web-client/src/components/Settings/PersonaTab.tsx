/**
 * Persona tab — character / personality the coordinator adopts in
 * its written replies.
 */
import React from 'react'
import f from './fields.module.css'
import { TextField } from './fields'
import { useTabForm } from './useTabForm'
import { usePublishTabForm } from './settingsFormContext'
import type { UseSettingsState } from '../../hooks/useSettingsState'

const FIELDS = ['LLM_PERSONA'] as const
type FieldKey = (typeof FIELDS)[number]

export const PersonaTab: React.FC<{ state: UseSettingsState }> = ({ state }) => {
  const form = useTabForm<Record<FieldKey, string>>({
    state,
    fields: FIELDS,
  })
  usePublishTabForm('persona', form.dirty, form.buildUpdates, form.reset)

  return (
    <div>
      <p className={f.tabIntro}>
        Give the assistant a character or tone for its written replies.
        Leave blank for a neutral voice.
      </p>

      <TextField
        id="persona-llm"
        label="Persona"
        value={form.values.LLM_PERSONA}
        onChange={(v) => form.setField('LLM_PERSONA', v)}
        placeholder='e.g. "Peter Griffin" or "A wise British butler"'
        help="Optional. Examples: a character (Peter Griffin, a wise British butler), a tone (sarcastic, formal), or a domain expert (a music historian)."
      />
    </div>
  )
}
