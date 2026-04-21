import { AppShell } from '@/components/layout/app-shell'
import { DashboardContent } from '@/components/dashboard/dashboard-content'
import { Toaster } from '@/components/ui/sonner'

export default function DashboardPage() {
  return (
    <AppShell>
      <DashboardContent />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
