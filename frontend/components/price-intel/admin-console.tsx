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
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  apiPost, patchCompetitorSettings, updatePriceIntelSettings, useCompetitors,
  usePriceIntelSettings,
} from '@/lib/price-intel/hooks'
import { parseCompetitorSettings } from '@/lib/price-intel/format'
import type { AdminSetting, Competitor } from '@/lib/price-intel/types'
import {
  BellOff, CalendarClock, GitMerge, MessageSquare, RotateCcw, Send, Sparkles,
} from 'lucide-react'

type Changes = Record<string, string | number | boolean | null>

const DIRECTION_LABEL: Record<string, string> = {
  both: 'Drops & increases',
  drops: 'Drops only',
  increases: 'Increases only',
}

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

function SwitchRow({ label, hint, checked, onChange, disabled }: {
  label: string
  hint?: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <div className={disabled ? 'flex items-center justify-between gap-4 opacity-60'
                             : 'flex items-center justify-between gap-4'}>
      <div>
        <Label className="text-sm">{label}</Label>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </div>
  )
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <p className="border-t pt-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </p>
  )
}

// Per-store Slack switches. Each flip is its own PATCH (merged on the server)
// with an optimistic SWR update, so it never waits on — or races — the
// "Save Slack settings" button, and the Competitors tab sees the same row.
function StoresInSlack() {
  const { competitors, mutate } = useCompetitors()
  const [pending, setPending] = useState<Set<string>>(new Set())
  const stores = competitors.filter((c) => c.enabled && c.connector_type !== 'benchmark')

  const setInSlack = async (store: Competitor, inSlack: boolean) => {
    const settings = parseCompetitorSettings(store.settings_json)
    const next = { ...settings }
    if (inSlack) delete next.mute_slack
    else next.mute_slack = true
    const optimistic = competitors.map((c) => c.competitor_id === store.competitor_id
      ? { ...c, settings_json: Object.keys(next).length ? JSON.stringify(next) : null }
      : c)
    setPending((prev) => new Set(prev).add(store.competitor_id))
    try {
      await mutate(optimistic, { revalidate: false })
      await patchCompetitorSettings(store.competitor_id, { mute_slack: inSlack ? null : true })
      await mutate()
      toast.success(inSlack ? `${store.name} is back in Slack` : `${store.name} muted in Slack`)
    } catch (e) {
      await mutate() // back to the server's truth
      toast.error(e instanceof Error ? e.message : 'Failed to update store')
    } finally {
      setPending((prev) => {
        const n = new Set(prev)
        n.delete(store.competitor_id)
        return n
      })
    }
  }

  if (stores.length === 0) {
    return <p className="text-xs text-muted-foreground">No enabled stores yet.</p>
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {stores.map((store) => {
        const settings = parseCompetitorSettings(store.settings_json)
        const inSlack = settings.mute_slack !== true
        // A feed-muted family never reaches Slack regardless of this switch;
        // say so, or the switch looks like it does nothing.
        const feedMuted = [
          settings.mute_price_alerts && 'price',
          settings.mute_map_alerts && 'MAP',
        ].filter(Boolean) as string[]
        return (
          <div key={store.competitor_id}
               className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-sm">{store.name}</p>
              {feedMuted.length > 0 && (
                <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <BellOff className="h-3 w-3" />
                  {feedMuted.join(' & ')} alerts muted in the app (never reach Slack)
                </p>
              )}
            </div>
            <Switch checked={inSlack} disabled={pending.has(store.competitor_id)}
                    onCheckedChange={(v) => void setInSlack(store, v)}
                    aria-label={`${store.name} in Slack`} />
          </div>
        )
      })}
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
      const status = (ok: boolean | undefined) => (ok ? 'ok' : 'failed')
      toast.success(
        `Test sent — MAP ping: ${status(r.map_ping)}, price changes: ${status(r.price_changes)}, `
        + `digest: ${status(r.digest)}`
        + (r.undercut_ping !== undefined ? `, undercut ping: ${status(r.undercut_ping)}` : '')
        + ` (${res.alerts_webhook} webhook)`
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

  // --- section: digest -------------------------------------------------------
  const digestEnabled = Boolean(val('digest_enabled'))
  const promptDefault = String(byKey.digest_prompt?.default ?? '')
  const promptValue = String(val('digest_prompt') ?? '')
  const digestOverridden = Boolean(byKey.digest_prompt?.overridden)
    || Boolean(byKey.digest_enabled?.overridden)

  // --- section: schedule -----------------------------------------------------
  const hour = Number(val('schedule_hour') ?? 2)
  const minute = Number(val('schedule_minute') ?? 30)
  const timeValue = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  const scheduleKeys = ['schedule_enabled', 'schedule_hour', 'schedule_minute', 'schedule_timezone']

  // --- section: slack ---------------------------------------------------------
  const slackToggleKeys = [
    'slack_enabled', 'slack_send_digest', 'slack_map_pings', 'slack_undercut_pings',
    'slack_health_alerts', 'slack_max_priority_pings', 'slack_price_changes',
    'slack_price_change_min_pct', 'slack_price_change_directions', 'slack_max_price_changes',
  ]
  const webhookConfigured = (key: string) => Boolean(byKey[key]?.value)
  const directionChoices = byKey.slack_price_change_directions?.choices
    ?? ['both', 'drops', 'increases']

  const numberField = (key: string, label: string, fallback: number, min: number, max: number,
                       hint?: string) => (
    <div className="space-y-1.5">
      <Label className="text-sm">{label}</Label>
      <Input
        type="number" min={min} max={max}
        value={String(val(key) ?? fallback)}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10)
          setVal(key, Number.isNaN(n) ? min : Math.min(max, Math.max(min, n)))
        }}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <SectionCard
        icon={<Sparkles className="h-4 w-4 text-violet-600" />}
        title="LLM market digest"
        overridden={digestOverridden}
        description="A short market narrative written by the model after each nightly run, shown in the Digest tab and optionally posted to Slack."
      >
        <SwitchRow
          label="Generate digest after each nightly run"
          hint={digestEnabled
            ? 'Off = no LLM call at night. The Digest tab keeps the last one and its Regenerate button still works.'
            : 'Nightly generation is off. Regenerate in the Digest tab still works; the Slack post is paused.'}
          checked={digestEnabled}
          onChange={(v) => {
            setVal('digest_enabled', v)
            void save({ digest_enabled: v },
              v ? 'Nightly digest enabled' : 'Nightly digest disabled')
          }}
        />
        <div className="space-y-1.5">
          <Label className="text-sm">System prompt</Label>
          <p className="text-xs text-muted-foreground">
            Leave as-is to use the built-in prompt; edits apply from the next digest.
          </p>
          <Textarea
            value={promptValue}
            onChange={(e) => setVal('digest_prompt', e.target.value)}
            rows={12}
            className="font-mono text-xs leading-relaxed"
            spellCheck={false}
          />
        </div>
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
        description="Post-run messages, each behind its own switch: MAP/undercut pings, competitor price changes, the LLM digest, and scrape-health alerts. Store switches at the bottom apply to all of them."
      >
        <SwitchRow
          label="Slack notifications"
          hint="Master switch for all post-run messages."
          checked={Boolean(val('slack_enabled'))}
          onChange={(v) => setVal('slack_enabled', v)}
        />
        <div className="space-y-3">
          {([
            ['slack_webhook_url', 'Main webhook', 'Price changes, digest + health alerts'],
            ['slack_alerts_webhook_url', 'Alerts webhook', 'MAP / undercut pings (falls back to main when unset)'],
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

        <SubHeading>Alerts</SubHeading>
        <div className="grid gap-3 sm:grid-cols-2">
          <SwitchRow label="MAP violation pings"
                     hint="One red ping per listing that crossed below our MAP floor."
                     checked={Boolean(val('slack_map_pings'))}
                     onChange={(v) => setVal('slack_map_pings', v)} />
          <SwitchRow label="Undercut pings"
                     hint="One red ping per listing that crossed below our price on a non-MAP item."
                     checked={Boolean(val('slack_undercut_pings'))}
                     onChange={(v) => setVal('slack_undercut_pings', v)} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {numberField('slack_max_priority_pings', 'Max pings per run', 15, 0, 100,
                       'The rest roll up into one “…and N more” line.')}
        </div>

        <SubHeading>Competitor price changes</SubHeading>
        <SwitchRow
          label="Price changes message"
          hint="One message per run: the change feed's price drops and increases rolled up per product × store, e.g. “SuperSix EVO 5 (6 variants) +20% → $3,199.93 — Primeau Velo”."
          checked={Boolean(val('slack_price_changes'))}
          onChange={(v) => setVal('slack_price_changes', v)}
        />
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label className="text-sm">Direction</Label>
            <Select value={String(val('slack_price_change_directions') ?? 'both')}
                    onValueChange={(v) => setVal('slack_price_change_directions', v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {directionChoices.map((c) => (
                  <SelectItem key={c} value={c}>{DIRECTION_LABEL[c] ?? c}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {numberField('slack_price_change_min_pct', 'Min % change', 0, 0, 100,
                       '0 = every change the feed records.')}
          {numberField('slack_max_price_changes', 'Max products per message', 15, 1, 50)}
        </div>

        <SubHeading>Digest &amp; health</SubHeading>
        <div className="grid gap-3 sm:grid-cols-2">
          <SwitchRow label="Post digest to Slack"
                     hint={digestEnabled
                       ? 'The LLM narrative, posted ahead of the price changes.'
                       : 'Turn on digest generation (LLM market digest card) to post it.'}
                     disabled={!digestEnabled}
                     checked={Boolean(val('slack_send_digest'))}
                     onChange={(v) => setVal('slack_send_digest', v)} />
          <SwitchRow label="Scrape health alerts"
                     hint="Red message when a run fails or finishes partial."
                     checked={Boolean(val('slack_health_alerts'))}
                     onChange={(v) => setVal('slack_health_alerts', v)} />
        </div>

        <SubHeading>Stores in Slack</SubHeading>
        <p className="text-xs text-muted-foreground">
          Off keeps a store in the change feed but out of every Slack message (for undercut
          pings this is the only per-store mute). Saves immediately.
        </p>
        <StoresInSlack />

        <div className="flex items-center justify-between gap-2 border-t pt-3">
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
