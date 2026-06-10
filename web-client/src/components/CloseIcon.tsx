interface CloseIconProps {
  size?: number
}

/** A geometrically-centred close glyph — an SVG "X" rather than the "×"
 *  character, which sits slightly high and right in its own glyph box and so
 *  never centres cleanly in a button. Inherits colour via currentColor. */
export function CloseIcon({ size = 12 }: CloseIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 6 18 18M18 6 6 18" />
    </svg>
  )
}
