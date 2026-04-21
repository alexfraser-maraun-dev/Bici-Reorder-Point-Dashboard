import { AppShell } from '@/components/layout/app-shell'
import { SettingsContent } from '@/components/pages/settings-content'
import { Toaster } from '@/components/ui/sonner'

export default function SettingsPage() {
  return (
    <AppShell>
      <SettingsContent />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
