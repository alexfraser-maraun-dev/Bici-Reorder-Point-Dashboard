'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Separator } from '@/components/ui/separator'
import { Spinner } from '@/components/ui/spinner'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { FieldGroup, Field, FieldLabel } from '@/components/ui/field'
import { Save, RotateCcw } from 'lucide-react'

export function SettingsContent() {
  const [isSaving, setIsSaving] = useState(false)

  // Default settings
  const [settings, setSettings] = useState({
    defaultTrailingDays: 90,
    forecastDays: 30,
    safetyDays: 7,
    showMonthlyCadence: true,
    defaultLeadTimeDays: 14,
    defaultSafetyStock: 5,
  })

  // Location policy defaults
  const [locationPolicies, setLocationPolicies] = useState([
    { location: 'Downtown Store', safetyDays: 7, leadTimeDays: 10 },
    { location: 'Mall Location', safetyDays: 5, leadTimeDays: 7 },
    { location: 'Outlet Center', safetyDays: 10, leadTimeDays: 14 },
    { location: 'Online Warehouse', safetyDays: 3, leadTimeDays: 5 },
    { location: 'Regional Distribution', safetyDays: 14, leadTimeDays: 21 },
  ])

  const handleSave = async () => {
    setIsSaving(true)
    // Simulate API call
    await new Promise((resolve) => setTimeout(resolve, 1000))
    setIsSaving(false)
    toast.success('Settings saved successfully')
  }

  const handleReset = () => {
    setSettings({
      defaultTrailingDays: 90,
      forecastDays: 30,
      safetyDays: 7,
      showMonthlyCadence: true,
      defaultLeadTimeDays: 14,
      defaultSafetyStock: 5,
    })
    toast.info('Settings reset to defaults')
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-muted-foreground text-sm">
            Configure default parameters for recommendation calculations
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReset}>
            <RotateCcw className="mr-2 h-4 w-4" />
            Reset
          </Button>
          <Button onClick={handleSave} disabled={isSaving}>
            {isSaving ? <Spinner className="mr-2 h-4 w-4" /> : <Save className="mr-2 h-4 w-4" />}
            Save Changes
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Calculation Parameters */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Calculation Parameters</CardTitle>
            <CardDescription>
              Default values used when calculating recommendations
            </CardDescription>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="trailing-days">Default Trailing Days</FieldLabel>
                <Input
                  id="trailing-days"
                  type="number"
                  value={settings.defaultTrailingDays}
                  onChange={(e) =>
                    setSettings({ ...settings, defaultTrailingDays: parseInt(e.target.value) || 0 })
                  }
                />
                <p className="text-muted-foreground text-xs">
                  Number of days of sales history to analyze
                </p>
              </Field>

              <Field>
                <FieldLabel htmlFor="forecast-days">Forecast Days</FieldLabel>
                <Input
                  id="forecast-days"
                  type="number"
                  value={settings.forecastDays}
                  onChange={(e) =>
                    setSettings({ ...settings, forecastDays: parseInt(e.target.value) || 0 })
                  }
                />
                <p className="text-muted-foreground text-xs">
                  Number of days to forecast for desired inventory level
                </p>
              </Field>

              <Field>
                <FieldLabel htmlFor="safety-days">Safety Days</FieldLabel>
                <Input
                  id="safety-days"
                  type="number"
                  value={settings.safetyDays}
                  onChange={(e) =>
                    setSettings({ ...settings, safetyDays: parseInt(e.target.value) || 0 })
                  }
                />
                <p className="text-muted-foreground text-xs">
                  Additional buffer days for safety stock calculation
                </p>
              </Field>
            </FieldGroup>
          </CardContent>
        </Card>

        {/* Display Settings */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Display Settings</CardTitle>
            <CardDescription>Configure how data is displayed in the dashboard</CardDescription>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <div className="flex items-center justify-between rounded-lg border p-3">
                <div className="space-y-0.5">
                  <Label className="text-sm font-medium">Monthly Cadence Display</Label>
                  <p className="text-muted-foreground text-xs">
                    Show monthly sales cadence in row details
                  </p>
                </div>
                <Switch
                  checked={settings.showMonthlyCadence}
                  onCheckedChange={(checked) =>
                    setSettings({ ...settings, showMonthlyCadence: checked })
                  }
                />
              </div>

              <Field>
                <FieldLabel htmlFor="default-lead-time">Default Lead Time (Days)</FieldLabel>
                <Input
                  id="default-lead-time"
                  type="number"
                  value={settings.defaultLeadTimeDays}
                  onChange={(e) =>
                    setSettings({ ...settings, defaultLeadTimeDays: parseInt(e.target.value) || 0 })
                  }
                />
                <p className="text-muted-foreground text-xs">
                  Default lead time when vendor-specific data is unavailable
                </p>
              </Field>

              <Field>
                <FieldLabel htmlFor="default-safety">Default Safety Stock (Units)</FieldLabel>
                <Input
                  id="default-safety"
                  type="number"
                  value={settings.defaultSafetyStock}
                  onChange={(e) =>
                    setSettings({ ...settings, defaultSafetyStock: parseInt(e.target.value) || 0 })
                  }
                />
                <p className="text-muted-foreground text-xs">
                  Minimum safety stock when calculated value is lower
                </p>
              </Field>
            </FieldGroup>
          </CardContent>
        </Card>

        {/* Location Policy Defaults */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Location Policy Defaults</CardTitle>
            <CardDescription>
              Configure default parameters per location. These override global defaults.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg border">
              <div className="bg-muted/50 grid grid-cols-3 gap-4 border-b px-4 py-2">
                <div className="text-sm font-medium">Location</div>
                <div className="text-sm font-medium">Safety Days</div>
                <div className="text-sm font-medium">Lead Time Days</div>
              </div>
              <div className="divide-y">
                {locationPolicies.map((policy, index) => (
                  <div key={policy.location} className="grid grid-cols-3 gap-4 px-4 py-3">
                    <div className="flex items-center">
                      <span className="text-sm">{policy.location}</span>
                    </div>
                    <div>
                      <Input
                        type="number"
                        value={policy.safetyDays}
                        onChange={(e) => {
                          const updated = [...locationPolicies]
                          updated[index].safetyDays = parseInt(e.target.value) || 0
                          setLocationPolicies(updated)
                        }}
                        className="h-8 w-24"
                      />
                    </div>
                    <div>
                      <Input
                        type="number"
                        value={policy.leadTimeDays}
                        onChange={(e) => {
                          const updated = [...locationPolicies]
                          updated[index].leadTimeDays = parseInt(e.target.value) || 0
                          setLocationPolicies(updated)
                        }}
                        className="h-8 w-24"
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
