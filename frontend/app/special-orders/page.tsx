import { AppShell } from '@/components/layout/app-shell'
import { SpecialOrdersContent } from '@/components/pages/special-orders-content'
import { Toaster } from '@/components/ui/sonner'

export default function SpecialOrdersPage() {
  return (
    <AppShell>
      <SpecialOrdersContent />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
