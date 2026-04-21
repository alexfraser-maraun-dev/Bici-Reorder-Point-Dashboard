'use client'

import { useRecommendationRuns } from '@/lib/hooks'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { CheckCircle2, XCircle, Loader2, Clock, Calendar, Settings2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export function RunsContent() {
  const { data: runs, isLoading } = useRecommendationRuns()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Recommendation Runs</h1>
        <p className="text-muted-foreground text-sm">
          History of automated recommendation calculations
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Run History</CardTitle>
          <CardDescription>
            Each run calculates new reorder points and desired inventory levels based on sales data
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Run Date</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead className="text-right">Total Rows</TableHead>
                    <TableHead className="text-right">Changed</TableHead>
                    <TableHead className="text-right">Needs Order</TableHead>
                    <TableHead>Settings</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} className="h-24 text-center">
                        <div className="text-muted-foreground">
                          <p className="font-medium">No runs found</p>
                          <p className="text-sm">Recommendation runs will appear here</p>
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : (
                    runs.map((run) => (
                      <TableRow key={run.id}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Calendar className="text-muted-foreground h-4 w-4" />
                            <div>
                              <p className="text-sm font-medium">
                                {new Date(run.runDate).toLocaleDateString('en-US', {
                                  month: 'short',
                                  day: 'numeric',
                                  year: 'numeric',
                                })}
                              </p>
                              <p className="text-muted-foreground text-xs">
                                {new Date(run.runDate).toLocaleTimeString('en-US', {
                                  hour: '2-digit',
                                  minute: '2-digit',
                                })}
                              </p>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <RunStatusBadge status={run.status} />
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <Clock className="text-muted-foreground h-3.5 w-3.5" />
                            <span className="text-sm">{run.duration}</span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right">
                          <span className="tabular-nums">{run.totalRows.toLocaleString()}</span>
                        </TableCell>
                        <TableCell className="text-right">
                          <span
                            className={cn(
                              'tabular-nums',
                              run.changedRows > 0 && 'font-medium text-blue-600'
                            )}
                          >
                            {run.changedRows.toLocaleString()}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">
                          <span
                            className={cn(
                              'tabular-nums',
                              run.needsOrderCount > 0 && 'font-medium text-amber-600'
                            )}
                          >
                            {run.needsOrderCount.toLocaleString()}
                          </span>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <Settings2 className="text-muted-foreground h-3.5 w-3.5" />
                            <span className="text-muted-foreground text-xs">
                              {run.trailingDays}d trailing, {run.forecastDays}d forecast,{' '}
                              {run.safetyDays}d safety
                            </span>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function RunStatusBadge({ status }: { status: 'completed' | 'running' | 'failed' }) {
  const config = {
    completed: {
      icon: CheckCircle2,
      label: 'Completed',
      className: 'bg-emerald-100 text-emerald-700 border-emerald-200',
    },
    running: {
      icon: Loader2,
      label: 'Running',
      className: 'bg-blue-100 text-blue-700 border-blue-200',
    },
    failed: {
      icon: XCircle,
      label: 'Failed',
      className: 'bg-red-100 text-red-700 border-red-200',
    },
  }

  const { icon: Icon, label, className } = config[status]

  return (
    <Badge variant="outline" className={cn('gap-1', className)}>
      <Icon className={cn('h-3 w-3', status === 'running' && 'animate-spin')} />
      {label}
    </Badge>
  )
}
