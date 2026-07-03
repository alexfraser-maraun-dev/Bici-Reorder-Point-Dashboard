import { AppShell } from '@/components/layout/app-shell'
import { PriceIntelligenceContent } from '@/components/pages/price-intelligence-content'
import { Toaster } from '@/components/ui/sonner'

export default function PriceIntelligencePage() {
  return (
    <AppShell>
      <PriceIntelligenceContent />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
