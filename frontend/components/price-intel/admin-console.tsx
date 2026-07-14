'use client'

// Admin tab: runtime configuration for the price-intel jobs. Each setting is
// an override stored in BigQuery (pi_settings) on top of its env-var default —
// "Reset to default" clears the override. Slack webhooks are secrets: the API
// only ever returns a masked tail, so the inputs here are write-only.

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  apiPost, updatePriceIntelSettings, usePriceIntelSettings,
} from '@/lib/price-intel/hooks'
import type { AdminSetting } from '@/lib/price-intel/types'
import {
  CalendarClock, GitMerge, MessageSquare, RotateCcw, Send, Sparkles,
} from 'lucide-react'

type Changes = Record<string, string | number | boolean | null>

function SectionCard({ icon, title, description, overridden, children }: {
  icon: React.ReactNode
  title: string
  description: string
  overridden?: boolean
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            {icon}
            <h3 className="text-sm font-semibold">{title}</h3>
            {overridden && (
              <Badge variant="outline" className="text-[11px] text-amber-600 border-amber-300">
                customized
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        {children}
      </CardContent>
    </Card>
  )
}

function SwitchRow({ label, hint, checked, onChange }: {
  label: string
  hint?: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <Label className="text-sm">{label}</Label>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  )
}

export function AdminConsole() {
  const { settings, isLoading, mutate } = usePriceIntelSettings()
  const [saving, setSaving] = useState(false)
  const [testingSlack, setTestingSlack] = useState(false)
  // Local edits keyed by setting key; unset keys render the server value.
  const [edits, setEdits] = useState<Changes>({})

  const byKey = useMemo(() => {
    const map: Record<string, AdminSetting> = {}
    for (const s of settings) map[s.key] = s
    return map
  }, [settings])

  const serverValue = (key: string) => byKey[key]?.value
  const val = (key: string) => (key in edits ? edits[key] : serverValue(key))
  const setVal = (key: string, value: Changes[string]) =>
    setEdits((prev) => ({ ...prev, [key]: value }))
  const isDirty = (keys: string[]) =>
    keys.some((k) => k in edits && edits[k] !== serverValue(k))

  const save = async (changes: Changes, successMsg = 'Settings saved') => {
    setSaving(true)
    try {
      const res = await updatePriceIntelSettings(changes)
      await mutate(res, { revalidate: false })
      setEdits((prev) => {
        const next = { ...prev }
        for (const k of Object.keys(changes)) delete next[k]
        return next
      })
      toast.success(successMsg)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const saveDirty = (keys: string[]) => {
    const changes: Changes = {}
    for (const k of keys) {
      if (k in edits && edits[k] !== serverValue(k)) changes[k] = edits[k]
    }
    if (Object.keys(changes).length) void save(changes)
  }

  const sendSlackTest = async () => {
    setTestingSlack(true)
    try {
      const res = await apiPost('/api/price-intel/notify/test')
      const r = res?.results ?? {}
      toast.success(
        `Test sent — digest: ${r.digest ? 'ok' : 'failed'}, MAP ping: ${r.map_ping ? 'ok' : 'failed'} (${res.alerts_webhook} webhook)`
      )
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Slack test failed')
    } finally {
      setTestingSlack(false)
    }
  }

  if (isLoading) {
    return (
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-56 rounded-xl" />)}
      </div>
    )
  }

  // --- section: digest prompt ------------------------------------------------
  const promptDefault = String(byKey.digest_prompt?.default ?? '')
  const promptValue = String(val('digest_prompt') ?? '')
  const promptOverridden = Boolean(byKey.digest_prompt?.overridden)

  // --- section: schedule -----------------------------------------------------
  const hour = Number(val('schedule_hour') ?? 2)
  const minute = Number(val('schedule_minute') ?? 30)
  const timeValue = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  const scheduleKeys = ['schedule_enabled', 'schedule_hour', 'schedule_minute', 'schedule_timezone']

  // --- section: slack ---------------------------------------------------------
  const slackToggleKeys = [
    'slack_enabled', 'slack_send_digest', 'slack_map_pings', 'slack_health_alerts',
    'slack_max_priority_pings', 'slack_max_digest_moves',
  ]
  const webhookConfigured = (key: string) => Boolean(byKey[key]?.value)

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <SectionCard
        icon={<Sparkles className="h-4 w-4 text-violet-600" />}
        title="Daily digest prompt"
        overridden={promptOverridden}
        description="System prompt for the LLM market digest generated after each nightly run. Leave as-is to use the built-in prompt; edits apply from the next digest."
      >
        <Textarea
          value={promptValue}
          onChange={(e) => setVal('digest_prompt', e.target.value)}
          rows={14}
          className="font-mono text-xs leading-relaxed"
          spellCheck={false}
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground">
            {promptValue.trim().length.toLocaleString()} characters
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline" size="sm" disabled={saving}
              onClick={() => {
                setVal('digest_prompt', promptDefault)
                void save({ digest_prompt: null }, 'Digest prompt reset to default')
              }}
            >
              <RotateCcw className="h-4 w-4" /> Reset to default
            </Button>
            <Button
              size="sm"
              disabled={saving || !isDirty(['digest_prompt'])}
              onClick={() => {
                const text = promptValue.trim()
                // Saving the default text (or nothing) just clears the override.
                void save(
                  { digest_prompt: !text || text === promptDefault.trim() ? null : text },
                  'Digest prompt saved'
                )
              }}
            >
              Save prompt
            </Button>
          </div>
        </div>
      </SectionCard>

      <div className="space-y-4">
        <SectionCard
          icon={<CalendarClock className="h-4 w-4 text-sky-600" />}
          title="Daily run timing"
          overridden={scheduleKeys.some((k) => byKey[k]?.overridden)}
          description="When the nightly scrape (and everything downstream: matching, digest, Slack) kicks off. The scheduler checks every minute, so changes take effect immediately."
        >
          <SwitchRow
            label="Nightly scheduled run"
            hint="Off = scrapes only run when triggered manually."
            checked={Boolean(val('schedule_enabled'))}
            onChange={(v) => setVal('schedule_enabled', v)}
          />
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-sm">Start time</Label>
              <Input
                type="time"
                value={timeValue}
                onChange={(e) => {
                  const [h, m] = e.target.value.split(':').map((n) => parseInt(n, 10))
                  if (!Number.isNaN(h)) setVal('schedule_hour', h)
                  if (!Number.isNaN(m)) setVal('schedule_minute', m)
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm">Timezone (IANA)</Label>
              <Input
                value={String(val('schedule_timezone') ?? '')}
                onChange={(e) => setVal('schedule_timezone', e.target.value)}
                placeholder="America/Vancouver"
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Runs after the overnight Lightspeed → BigQuery sync lands (default 02:30).
          </p>
          <div className="flex justify-end">
            <Button size="sm" disabled={saving || !isDirty(scheduleKeys)}
                    onClick={() => saveDirty(scheduleKeys)}>
              Save schedule
            </Button>
          </div>
        </SectionCard>

        <SectionCard
          icon={<GitMerge className="h-4 w-4 text-emerald-600" />}
          title="Match confirmation"
          overridden={Boolean(byKey.auto_confirm?.overridden)}
          description="How competitor listings get linked to your products."
        >
          <SwitchRow
            label="Auto-confirm high-confidence matches"
            hint="On: barcode (GTIN) hits and LLM same-variant verdicts confirm themselves. Off: every proposed match waits in the Matching queue for your review — clear non-matches are still auto-rejected."
            checked={Boolean(val('auto_confirm'))}
            onChange={(v) => {
              setVal('auto_confirm', v)
              void save({ auto_confirm: v },
                v ? 'Auto-confirm enabled' : 'Manual review enabled')
            }}
          />
          <p className="text-xs text-muted-foreground">
            Applies from the next scrape run; already-confirmed links are unaffected.
          </p>
        </SectionCard>
      </div>

      <SectionCard
        icon={<MessageSquare className="h-4 w-4 text-rose-600" />}
        title="Slack messaging"
        overridden={slackToggleKeys.some((k) => byKey[k]?.overridden)
          || Boolean(byKey.slack_webhook_url?.overridden)
          || Boolean(byKey.slack_alerts_webhook_url?.overridden)}
        description="Post-run notifications: the market digest, MAP-violation pings, and scrape-health alerts."
      >
        <SwitchRow
          label="Slack notifications"
          hint="Master switch for all post-run messages."
          checked={Boolean(val('slack_enabled'))}
          onChange={(v) => setVal('slack_enabled', v)}
        />
        <div className="space-y-3">
          {([
            ['slack_webhook_url', 'Main webhook', 'Digest + health alerts'],
            ['slack_alerts_webhook_url', 'Alerts webhook', 'MAP pings (falls back to main when unset)'],
          ] as const).map(([key, label, hint]) => (
            <div key={key} className="space-y-1.5">
              <div className="flex items-center gap-2">
                <Label className="text-sm">{label}</Label>
                <span className="text-xs text-muted-foreground">{hint}</span>
                {webhookConfigured(key) && (
                  <Badge variant="outline" className="text-[11px]">
                    set: {String(byKey[key]?.value)}
                  </Badge>
                )}
              </div>
              <div className="flex gap-2">
                <Input
                  type="password"
                  autoComplete="off"
                  value={String(key in edits ? edits[key] ?? '' : '')}
                  onChange={(e) => setVal(key, e.target.value)}
                  placeholder={webhookConfigured(key)
                    ? 'Enter a new URL to replace'
                    : 'https://hooks.slack.com/services/…'}
                />
                {byKey[key]?.overridden && (
                  <Button variant="outline" size="sm" disabled={saving}
                          onClick={() => void save({ [key]: null }, `${label} reset`)}>
                    <RotateCcw className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <SwitchRow label="Daily digest message"
                     checked={Boolean(val('slack_send_digest'))}
                     onChange={(v) => setVal('slack_send_digest', v)} />
          <SwitchRow label="MAP violation pings"
                     checked={Boolean(val('slack_map_pings'))}
                     onChange={(v) => setVal('slack_map_pings', v)} />
          <SwitchRow label="Scrape health alerts"
                     checked={Boolean(val('slack_health_alerts'))}
                     onChange={(v) => setVal('slack_health_alerts', v)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-sm">Max MAP pings per run</Label>
            <Input
              type="number" min={0} max={100}
              value={String(val('slack_max_priority_pings') ?? 15)}
              onChange={(e) => setVal('slack_max_priority_pings', parseInt(e.target.value, 10) || 0)}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm">Max moves in digest</Label>
            <Input
              type="number" min={0} max={100}
              value={String(val('slack_max_digest_moves') ?? 15)}
              onChange={(e) => setVal('slack_max_digest_moves', parseInt(e.target.value, 10) || 0)}
            />
          </div>
        </div>
        <div className="flex items-center justify-between gap-2">
          <Button variant="outline" size="sm" disabled={testingSlack}
                  onClick={() => void sendSlackTest()}>
            <Send className="h-4 w-4" /> Send test messages
          </Button>
          <Button
            size="sm"
            disabled={saving || !(
              isDirty(slackToggleKeys)
              || Boolean(edits.slack_webhook_url)
              || Boolean(edits.slack_alerts_webhook_url)
            )}
            onClick={() => {
              const changes: Changes = {}
              for (const k of slackToggleKeys) {
                if (k in edits && edits[k] !== serverValue(k)) changes[k] = edits[k]
              }
              for (const k of ['slack_webhook_url', 'slack_alerts_webhook_url']) {
                const typed = String(edits[k] ?? '').trim()
                if (typed) changes[k] = typed
              }
              if (Object.keys(changes).length) void save(changes, 'Slack settings saved')
            }}
          >
            Save Slack settings
          </Button>
        </div>
      </SectionCard>
    </div>
  )
}
