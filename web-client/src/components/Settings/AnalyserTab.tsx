/**
 * Conversation analyser tuning — frequency, batch size, staleness.
 *
 * The analyser's enable toggle and its LLM model live on the Models
 * tab. This tab covers only the runtime tuning of the background scan
 * loop. The "Scan & Analyse" / "Re-Analyse" buttons in the analysis
 * browser work on demand even when the background loop is off.
 */
import React from 'react'
import f from './fields.module.css'
import { NumberField } from './fields'
import { useTabForm } from './useTabForm'
import { usePublishTabForm } from './settingsFormContext'
import type { UseSettingsState } from '../../hooks/useSettingsState'

const FIELDS = [
  'ANALYSER_INTERVAL_MINUTES',
  'ANALYSER_STALENESS_MINUTES',
  'ANALYSER_BATCH_SIZE',
] as const
type FieldKey = (typeof FIELDS)[number]

export const AnalyserTab: React.FC<{ state: UseSettingsState }> = ({ state }) => {
  const form = useTabForm<Record<FieldKey, string>>({
    state,
    fields: FIELDS,
  })
  usePublishTabForm('analyser', form.dirty, form.buildUpdates, form.reset)

  return (
    <div>
      <p className={f.tabIntro}>
        Scheduling for the background analyser. Turn the analyser on
        and pick its model on the <strong>Models</strong> tab — these
        settings only take effect when it's switched on.
      </p>

      <NumberField
        id="analyser-interval"
        label="Scan interval (minutes)"
        value={form.values.ANALYSER_INTERVAL_MINUTES}
        onChange={(v) => form.setField('ANALYSER_INTERVAL_MINUTES', v)}
        min={1}
        step={1}
        placeholder="30"
        help="How often to check for new conversations to review."
      />

      <NumberField
        id="analyser-staleness"
        label="Staleness threshold (minutes)"
        value={form.values.ANALYSER_STALENESS_MINUTES}
        onChange={(v) => form.setField('ANALYSER_STALENESS_MINUTES', v)}
        min={1}
        step={1}
        placeholder="60"
        help="Wait this long after a conversation goes quiet before reviewing it — avoids reviewing chats that aren't really finished."
      />

      <NumberField
        id="analyser-batch-size"
        label="Batch size"
        value={form.values.ANALYSER_BATCH_SIZE}
        onChange={(v) => form.setField('ANALYSER_BATCH_SIZE', v)}
        min={1}
        step={1}
        placeholder="5"
        help="How many conversations to review in one go. Higher gets through a backlog faster; very high values may exceed the model's input limit."
      />
    </div>
  )
}
