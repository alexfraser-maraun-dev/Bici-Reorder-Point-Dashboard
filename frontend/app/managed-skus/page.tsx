'use client'

import { AppShell } from '@/components/layout/app-shell'
import { ManagedSkusContent } from '@/components/pages/managed-skus-content'
import { Toaster } from '@/components/ui/sonner'

export const dynamic = 'force-dynamic'

export default function ManagedSkusPage() {
  return (
    <AppShell>
      <ManagedSkusContent />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
