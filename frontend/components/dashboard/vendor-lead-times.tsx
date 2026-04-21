'use client'

import { useVendorLeadTimes } from '@/lib/hooks'
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'
import { Truck, AlertCircle, MapPin } from 'lucide-react'

export function VendorLeadTimes() {
  const { data, isLoading } = useVendorLeadTimes()

  const leadTimes = data?.data || []

  return (
    <div className="bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col h-full animate-in fade-in duration-500">
      <div className="p-4 border-b bg-muted/20 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Truck className="w-5 h-5 text-blue-600" />
          <h2 className="font-semibold text-lg">Vendor Lead Times Matrix</h2>
        </div>
        <div className="flex items-center gap-4 text-[10px] font-bold uppercase text-muted-foreground">
          <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-blue-500" /> Adanac</div>
          <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-emerald-500" /> Langford</div>
          <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-purple-500" /> Victoria</div>
        </div>
      </div>
      
      <div className="overflow-auto flex-1">
        <Table>
          <TableHeader className="bg-muted/50 sticky top-0 z-10 backdrop-blur-sm">
            <TableRow>
              <TableHead className="w-[200px]">Vendor Name</TableHead>
              <TableHead className="text-center bg-blue-50/30 dark:bg-blue-900/10">Adanac Lead</TableHead>
              <TableHead className="text-center bg-blue-50/30 dark:bg-blue-900/10 border-r">Adanac POs</TableHead>
              <TableHead className="text-center bg-emerald-50/30 dark:bg-emerald-900/10">Langford Lead</TableHead>
              <TableHead className="text-center bg-emerald-50/30 dark:bg-emerald-900/10 border-r">Langford POs</TableHead>
              <TableHead className="text-center bg-purple-50/30 dark:bg-purple-900/10">Victoria Lead</TableHead>
              <TableHead className="text-center bg-purple-50/30 dark:bg-purple-900/10">Victoria POs</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 15 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 7 }).map((_, j) => (
                    <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
                  ))}
                </TableRow>
              ))
            ) : leadTimes.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-64 text-center text-muted-foreground">
                  <div className="flex flex-col items-center gap-2">
                    <AlertCircle className="w-8 h-8 opacity-20" />
                    <p>No vendor lead times found in the spreadsheet.</p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              leadTimes.map((vendor: any, idx: number) => (
                <TableRow key={idx} className="hover:bg-muted/30 transition-colors">
                  <TableCell className="font-semibold text-xs py-3 border-r">
                    {vendor.vendor}
                  </TableCell>
                  
                  <TableCell className="text-center tabular-nums text-blue-600 font-bold bg-blue-50/5">
                    {vendor.adanac_lead} d
                  </TableCell>
                  <TableCell className="text-center tabular-nums text-muted-foreground text-[10px] bg-blue-50/5 border-r">
                    {vendor.adanac_pos}
                  </TableCell>

                  <TableCell className="text-center tabular-nums text-emerald-600 font-bold bg-emerald-50/5">
                    {vendor.langford_lead} d
                  </TableCell>
                  <TableCell className="text-center tabular-nums text-muted-foreground text-[10px] bg-emerald-50/5 border-r">
                    {vendor.langford_pos}
                  </TableCell>

                  <TableCell className="text-center tabular-nums text-purple-600 font-bold bg-purple-50/5">
                    {vendor.victoria_lead} d
                  </TableCell>
                  <TableCell className="text-center tabular-nums text-muted-foreground text-[10px] bg-purple-50/5">
                    {vendor.victoria_pos}
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
