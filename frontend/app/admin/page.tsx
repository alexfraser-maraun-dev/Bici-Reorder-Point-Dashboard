import { AppShell } from '@/components/layout/app-shell'
import { FeatureGate } from '@/components/layout/feature-gate'
import { AdminSettingsContent } from '@/components/pages/admin-settings-content'
import { Toaster } from '@/components/ui/sonner'

export default function AdminPage() {
  return (
    <AppShell>
      <FeatureGate feature="admin">
        <AdminSettingsContent />
      </FeatureGate>
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
