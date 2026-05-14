'use client'

import { AppShell } from '@/components/layout/app-shell'
import { AuditContent } from '@/components/pages/audit-content'
import { Toaster } from '@/components/ui/sonner'

export const dynamic = 'force-dynamic'

export default function AuditPage() {
  return (
    <AppShell>
      <AuditContent />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
