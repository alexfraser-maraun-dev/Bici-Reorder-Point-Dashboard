'use client'

import { useWritebackAudit } from '@/lib/hooks'
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
import { CheckCircle2, XCircle, ArrowRight, MapPin } from 'lucide-react'
import { cn } from '@/lib/utils'

export function AuditContent() {
  const { data: entries, isLoading } = useWritebackAudit()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Writeback Audit</h1>
        <p className="text-muted-foreground text-sm">
          Track all changes pushed to Lightspeed R-Series
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Audit Log</CardTitle>
          <CardDescription>
            Complete history of reorder point and desired level updates
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 10 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>SKU</TableHead>
                    <TableHead>Location</TableHead>
                    <TableHead>Reorder Point</TableHead>
                    <TableHead>Desired Level</TableHead>
                    <TableHead>Triggered By</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} className="h-24 text-center">
                        <div className="text-muted-foreground">
                          <p className="font-medium">No audit entries</p>
                          <p className="text-sm">Writeback attempts will be logged here</p>
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : (
                    entries.map((entry: any, idx: number) => (
                      <TableRow
                        key={entry.id ?? idx}
                        className={cn(entry.status === 'failed' && 'bg-red-50/50')}
                      >
                        <TableCell>
                          <div>
                            <p className="text-sm">
                              {new Date(entry.created_at ?? entry.timestamp).toLocaleDateString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                year: 'numeric',
                              })}
                            </p>
                            <p className="text-muted-foreground text-xs">
                              {new Date(entry.created_at ?? entry.timestamp).toLocaleTimeString('en-US', {
                                hour: '2-digit',
                                minute: '2-digit',
                              })}
                            </p>
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className="font-mono text-xs font-medium">{entry.sku}</span>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <MapPin className="text-muted-foreground h-3.5 w-3.5" />
                            <span className="max-w-[120px] truncate text-sm">{entry.location_id ?? entry.location}</span>
                          </div>
                        </TableCell>
                        {/* Reorder Point change */}
                        <TableCell>
                          <div className="flex items-center gap-1.5 tabular-nums text-sm">
                            <span className="text-muted-foreground">{entry.old_reorder_point ?? entry.oldValue ?? '—'}</span>
                            <ArrowRight className="text-muted-foreground h-3 w-3 shrink-0" />
                            <span className="font-medium text-blue-600">{entry.new_reorder_point ?? entry.newValue ?? '—'}</span>
                          </div>
                        </TableCell>
                        {/* Desired Level change */}
                        <TableCell>
                          <div className="flex items-center gap-1.5 tabular-nums text-sm">
                            <span className="text-muted-foreground">{entry.old_desired_inventory ?? '—'}</span>
                            <ArrowRight className="text-muted-foreground h-3 w-3 shrink-0" />
                            <span className="font-medium text-blue-600">{entry.new_desired_inventory ?? '—'}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className="text-muted-foreground text-xs">{entry.triggered_by ?? 'UI_Manual_Push'}</span>
                        </TableCell>
                        <TableCell>
                          <AuditStatusBadge
                            status={entry.status}
                            errorMessage={entry.error_message ?? entry.errorMessage}
                          />
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

function AuditStatusBadge({
  status,
  errorMessage,
}: {
  status: 'success' | 'failed'
  errorMessage?: string
}) {
  if (status === 'success') {
    return (
      <Badge
        variant="outline"
        className="gap-1 border-emerald-200 bg-emerald-100 text-emerald-700"
      >
        <CheckCircle2 className="h-3 w-3" />
        Success
      </Badge>
    )
  }

  return (
    <div className="flex flex-col gap-1">
      <Badge variant="outline" className="gap-1 border-red-200 bg-red-100 text-red-700">
        <XCircle className="h-3 w-3" />
        Failed
      </Badge>
      {errorMessage && (
        <span className="max-w-[150px] truncate text-xs text-red-600" title={errorMessage}>
          {errorMessage}
        </span>
      )}
    </div>
  )
}
