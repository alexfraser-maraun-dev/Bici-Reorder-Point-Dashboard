'use client'

import { useState, useEffect } from 'react'
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { History, Download, AlertCircle, CheckCircle2, XCircle } from 'lucide-react'

export function WritebackLogs() {
  const [logs, setLogs] = useState([])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    fetchLogs()
  }, [])

  const fetchLogs = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8000/api/replenishment/logs')
      const data = await response.json()
      setLogs(data)
    } catch (error) {
      console.error("Failed to fetch logs:", error)
    } finally {
      setIsLoading(false)
    }
  }

  const exportToCSV = () => {
    if (logs.length === 0) return
    
    const headers = ["SKU", "Location", "Old ROP", "New ROP", "Old DL", "New DL", "Status", "Date"]
    const csvContent = [
      headers.join(","),
      ...logs.map((log: any) => [
        log.sku,
        log.location_id,
        log.old_reorder_point,
        log.new_reorder_point,
        log.old_desired_inventory,
        log.new_desired_inventory,
        log.status,
        new Date(log.created_at).toLocaleString()
      ].join(","))
    ].join("\n")

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement("a")
    const url = URL.createObjectURL(blob)
    link.setAttribute("href", url)
    link.setAttribute("download", `writeback_audit_${new Date().toISOString().split('T')[0]}.csv`)
    link.style.visibility = 'hidden'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  return (
    <div className="bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col h-full animate-in fade-in duration-500">
      <div className="p-4 border-b bg-muted/20 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <History className="w-5 h-5 text-purple-600" />
          <h2 className="font-semibold text-lg">Writeback Audit Trail</h2>
        </div>
        <Button 
          variant="outline" 
          size="sm" 
          className="gap-2 text-xs"
          onClick={exportToCSV}
          disabled={logs.length === 0}
        >
          <Download className="w-3.5 h-3.5" />
          Export Audit Log
        </Button>
      </div>
      
      <div className="overflow-auto flex-1">
        <Table>
          <TableHeader className="bg-muted/50 sticky top-0 z-10 backdrop-blur-sm">
            <TableRow>
              <TableHead>Date & Time</TableHead>
              <TableHead>SKU</TableHead>
              <TableHead>Location</TableHead>
              <TableHead className="text-right">ROP Update</TableHead>
              <TableHead className="text-right">DL Update</TableHead>
              <TableHead className="text-center">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 10 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 6 }).map((_, j) => (
                    <TableCell key={j}><div className="h-4 w-full bg-muted animate-pulse rounded" /></TableCell>
                  ))}
                </TableRow>
              ))
            ) : logs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-64 text-center text-muted-foreground">
                  <div className="flex flex-col items-center gap-2">
                    <AlertCircle className="w-8 h-8 opacity-20" />
                    <p>No writeback history found.</p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              logs.map((log: any) => (
                <TableRow key={log.id} className="hover:bg-muted/30 transition-colors">
                  <TableCell className="text-[11px] font-medium">
                    {new Date(log.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell className="font-mono text-[10px]">{log.sku}</TableCell>
                  <TableCell className="text-[11px]">{log.location_id}</TableCell>
                  <TableCell className="text-right text-[11px]">
                    <span className="text-muted-foreground line-through mr-2">{log.old_reorder_point}</span>
                    <span className="font-bold text-blue-600">→ {log.new_reorder_point}</span>
                  </TableCell>
                  <TableCell className="text-right text-[11px]">
                    <span className="text-muted-foreground line-through mr-2">{log.old_desired_inventory}</span>
                    <span className="font-bold text-purple-600">→ {log.new_desired_inventory}</span>
                  </TableCell>
                  <TableCell className="text-center">
                    <Badge variant={log.status === 'success' ? 'secondary' : 'destructive'} className="gap-1 px-1.5 py-0 text-[9px] uppercase">
                      {log.status === 'success' ? <CheckCircle2 className="w-2.5 h-2.5" /> : <XCircle className="w-2.5 h-2.5" />}
                      {log.status}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
