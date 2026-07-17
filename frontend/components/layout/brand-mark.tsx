'use client'

import { cn } from '@/lib/utils'

/**
 * BICI Pulse brand mark — the original "B" letterform with a live ECG trace
 * running through its center. Ported from the v0 "Basis" identity system.
 *
 * When `animated`, it renders as the inline loader: a faint flickering
 * baseline, a bright monitor-style beam sweeping along the trace, and a
 * gentle heartbeat scale on the whole mark (via the `pulse-beat` class).
 * Honors `prefers-reduced-motion` (animations hold static).
 */

/** Original, unaltered "B" letterform — path data preserved exactly. */
const B_PATH =
  'M549.084 99.4206C507.196 96.3925 423.925 115.57 423.925 115.57L172.598 169.065V0H0V540H529.906C646.486 540 741.869 444.617 741.869 328.037V298.262C741.869 187.738 657.084 106.991 549.084 99.4206ZM561.701 322.486C561.701 347.72 541.514 367.907 516.28 367.907H172.598V321.477L507.196 264.449C547.065 258.393 561.701 286.654 561.701 304.318V322.486Z'

/**
 * ECG / heartbeat trace running horizontally through the vertical center
 * (y = 270) of the 742 x 540 mark, with the QRS spike landing inside the
 * counter of the B.
 */
const ECG_PATH =
  'M0 270 L160 270 L196 270 L214 250 L232 270 L280 270 L300 300 L318 90 L338 450 L356 270 L392 270 L418 248 L440 270 L742 270'

type BrandMarkProps = {
  animated?: boolean
  bColor?: string
  lineColor?: string
  strokeWidth?: number
  className?: string
  title?: string
}

export function BrandMark({
  animated = false,
  bColor = 'currentColor',
  lineColor = 'var(--color-signal)',
  strokeWidth = 16,
  className,
  title = 'BICI Pulse',
}: BrandMarkProps) {
  return (
    <svg
      viewBox="0 0 742 540"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={title}
      className={cn('h-auto w-full overflow-visible', className)}
    >
      <title>{title}</title>

      {/* Unaltered B letterform */}
      <path d={B_PATH} fill={bColor} />

      {animated ? (
        <>
          {/* Faint continuous baseline of the trace */}
          <path
            className="ecg-base"
            d={ECG_PATH}
            fill="none"
            stroke={lineColor}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* Bright segment sweeping across like a live monitor */}
          <path
            className="ecg-beam"
            d={ECG_PATH}
            pathLength={1}
            fill="none"
            stroke={lineColor}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray="0.32 0.68"
          />
        </>
      ) : (
        <path
          d={ECG_PATH}
          fill="none"
          stroke={lineColor}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  )
}
