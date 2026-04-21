import { AppShell } from '@/components/layout/app-shell'
import { RunsContent } from '@/components/pages/runs-content'
import { Toaster } from '@/components/ui/sonner'

export default function RunsPage() {
  return (
    <AppShell>
      <RunsContent />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
