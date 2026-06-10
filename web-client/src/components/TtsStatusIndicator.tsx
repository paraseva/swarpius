import React from 'react'
import s from './TtsStatusIndicator.module.css'

export type TtsIndicatorPhase = 'sending' | 'playing'

interface Props {
  phase: TtsIndicatorPhase
}

/** Compact three-bar indicator shown next to a chat message while it's
 *  being spoken via TTS. Two phases:
 *  - `sending`: subtle pulse — request in flight, waiting for audio.
 *  - `playing`: full-height equaliser — audio is playing back.
 *  Idle state is not rendered (the indicator is absent between
 *  utterances). Respects prefers-reduced-motion. */
export const TtsStatusIndicator: React.FC<Props> = ({ phase }) => {
  const label = phase === 'sending' ? 'Preparing speech' : 'Speaking'
  return (
    <span
      className={`${s.indicator} ${s[phase]}`}
      role="status"
      aria-label={label}
      aria-live="polite"
    >
      <i className={s.bar} />
      <i className={s.bar} />
      <i className={s.bar} />
    </span>
  )
}
