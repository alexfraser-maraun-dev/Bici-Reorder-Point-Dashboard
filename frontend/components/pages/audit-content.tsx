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
import { CheckCircle2, XCircle, ArrowRight, User, MapPin } from 'lucide-react'
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
                    <TableHead>User</TableHead>
                    <TableHead>SKU</TableHead>
                    <TableHead>Location</TableHead>
                    <TableHead>Field</TableHead>
                    <TableHead>Change</TableHead>
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
                    entries.map((entry) => (
                      <TableRow
                        key={entry.id}
                        className={cn(entry.status === 'failed' && 'bg-red-50/50')}
                      >
                        <TableCell>
                          <div>
                            <p className="text-sm">
                              {new Date(entry.timestamp).toLocaleDateString('en-US', {
                                month: 'short',
                                day: 'numeric',
                              })}
                            </p>
                            <p className="text-muted-foreground text-xs">
                              {new Date(entry.timestamp).toLocaleTimeString('en-US', {
                                hour: '2-digit',
                                minute: '2-digit',
                                second: '2-digit',
                              })}
                            </p>
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <User className="text-muted-foreground h-3.5 w-3.5" />
                            <span className="max-w-[150px] truncate text-sm">{entry.user}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className="font-mono text-xs font-medium">{entry.sku}</span>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <MapPin className="text-muted-foreground h-3.5 w-3.5" />
                            <span className="max-w-[120px] truncate text-sm">{entry.location}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="text-xs">
                            {entry.field === 'reorder_point' ? 'Reorder Point' : 'Desired Level'}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <span className="text-muted-foreground tabular-nums text-sm">
                              {entry.oldValue}
                            </span>
                            <ArrowRight className="text-muted-foreground h-3 w-3" />
                            <span className="font-medium tabular-nums text-sm text-blue-600">
                              {entry.newValue}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <AuditStatusBadge
                            status={entry.status}
                            errorMessage={entry.errorMessage}
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
