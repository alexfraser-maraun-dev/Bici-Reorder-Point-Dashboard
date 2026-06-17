'use client'

import { Line, LineChart, YAxis } from 'recharts'
import { ChartContainer, type ChartConfig } from '@/components/ui/chart'

// Momentum label -> series color, reusing the dashboard's status palette.
const MOMENTUM_COLORS: Record<string, string> = {
  surging: 'var(--chart-1)',
  rising: 'var(--chart-1)',
  spiky: 'var(--chart-4)',
  flat: 'var(--muted-foreground)',
  cooling: 'var(--chart-3)',
  insufficient_data: 'var(--muted-foreground)',
}

interface VelocitySparklineProps {
  // Daily-sales velocity per window, ordered oldest -> newest (31-60d, 15-30d, 14d).
  values: number[]
  labels?: string[]
  momentum?: string
  className?: string
}

export function VelocitySparkline({
  values,
  labels = ['31-60d', '15-30d', '14d'],
  momentum,
  className,
}: VelocitySparklineProps) {
  const color = MOMENTUM_COLORS[momentum ?? 'flat'] ?? 'var(--chart-1)'
  const data = values.map((value, index) => ({
    label: labels[index] ?? `w${index}`,
    velocity: Number.isFinite(value) ? value : 0,
  }))

  const config: ChartConfig = { velocity: { label: 'Daily velocity', color } }

  return (
    <ChartContainer config={config} className={className ?? 'h-[64px] w-full'}>
      <LineChart data={data} margin={{ left: 4, right: 4, top: 6, bottom: 2 }}>
        <YAxis hide domain={[0, 'auto']} />
        <Line
          dataKey="velocity"
          type="monotone"
          stroke={color}
          strokeWidth={2}
          dot={{ r: 2.5, fill: color }}
          isAnimationActive={false}
        />
      </LineChart>
    </ChartContainer>
  )
}
