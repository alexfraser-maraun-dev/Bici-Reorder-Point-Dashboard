import { AppShell } from '@/components/layout/app-shell'
import { FeatureGate } from '@/components/layout/feature-gate'
import { SpecialOrdersContent } from '@/components/pages/special-orders-content'
import { Toaster } from '@/components/ui/sonner'

export default function SpecialOrdersPage() {
  return (
    <AppShell>
      <FeatureGate feature="special_orders">
        <SpecialOrdersContent />
      </FeatureGate>
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
