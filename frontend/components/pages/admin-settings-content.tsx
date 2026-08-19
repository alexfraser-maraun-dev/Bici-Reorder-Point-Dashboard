'use client'

// Admin page: which features exist, which are switched on, and who may see them.
//
// Two panels:
//   Features — a global on/off per surface. Off means off for everyone: the nav
//              entry and tab disappear, the components never mount, and the
//              backend refuses that feature's endpoints. Nothing dormant costs
//              anything.
//   People   — per-login-email role plus, within the features that are on, an
//              allow/deny override per feature.
//
// Everything here is read and written through /api/admin/*, which is itself
// admin-gated on the backend.

import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  AlertTriangle, Layers, Loader2, Plus, RotateCcw, Save, ShieldCheck, Trash2, User,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  deleteUserAccess, saveUserAccess, updateFeatures, useAccess, useAdminFeatures,
  useAdminUsers,
} from '@/lib/access/hooks'
import type { FeatureDefinition, UserAccessRecord } from '@/lib/access/types'

type OverrideValue = boolean | null

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : 'Something went wrong'
}

// ---------------------------------------------------------------------------
// Features panel
// ---------------------------------------------------------------------------

function FeaturesPanel() {
  const { features, isLoading, mutate } = useAdminFeatures()
  const { mutate: mutateAccess } = useAccess()
  const [pending, setPending] = useState<string | null>(null)

  const groups = useMemo(() => {
    const map = new Map<string, FeatureDefinition[]>()
    for (const feature of features) {
      const list = map.get(feature.group) ?? []
      list.push(feature)
      map.set(feature.group, list)
    }
    return [...map.entries()]
  }, [features])

  const apply = async (changes: Record<string, boolean | null>, key: string) => {
    setPending(key)
    try {
      await updateFeatures(changes)
      await Promise.all([mutate(), mutateAccess()])
      toast.success('Saved')
    } catch (e) {
      toast.error(errorMessage(e))
    } finally {
      setPending(null)
    }
  }

  // Skeleton only when there is genuinely nothing to show. Gating on isLoading
  // alone would re-skeleton over data SWR already has cached on a revalidate.
  if (isLoading && features.length === 0) {
    return (
      <div className="space-y-3">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full" />)}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {groups.map(([group, items]) => (
        <Card key={group}>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">{group}</CardTitle>
          </CardHeader>
          <CardContent className="divide-y p-0">
            {items.map((feature) => (
              <div key={feature.key} className="flex items-start justify-between gap-4 px-6 py-3">
                <div className="min-w-0 space-y-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{feature.label}</span>
                    <code className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      {feature.key}
                    </code>
                    {feature.always_on && (
                      <Badge variant="outline" className="text-[10px]">always on</Badge>
                    )}
                    {feature.admin_only && !feature.always_on && (
                      <Badge variant="outline" className="text-[10px]">admins only</Badge>
                    )}
                    {feature.customized && (
                      <Badge variant="outline" className="border-amber-300 text-[10px] text-amber-600">
                        customized
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{feature.description}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {feature.customized && !feature.always_on && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 gap-1.5 text-xs"
                      disabled={pending === feature.key}
                      onClick={() => apply({ [feature.key]: null }, feature.key)}
                      title={`Restore the default (${feature.default_enabled ? 'on' : 'off'})`}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      Reset
                    </Button>
                  )}
                  {pending === feature.key
                    ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                    : (
                      <Switch
                        checked={feature.enabled}
                        disabled={feature.always_on}
                        onCheckedChange={(v) => apply({ [feature.key]: v }, feature.key)}
                      />
                    )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// People panel
// ---------------------------------------------------------------------------

function UserRow({
  user, features, onSaved,
}: {
  user: UserAccessRecord
  features: FeatureDefinition[]
  onSaved: () => Promise<void>
}) {
  const [role, setRole] = useState(user.role)
  const [overrides, setOverrides] = useState<Record<string, OverrideValue>>(user.overrides ?? {})
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    setRole(user.role)
    setOverrides(user.overrides ?? {})
  }, [user])

  const dirty =
    role !== user.role ||
    JSON.stringify(cleaned(overrides)) !== JSON.stringify(user.overrides ?? {})

  const overrideCount = Object.values(overrides).filter((v) => v !== null).length

  const save = async () => {
    setSaving(true)
    try {
      await saveUserAccess({ email: user.email, role, overrides: cleaned(overrides) })
      await onSaved()
      toast.success(`Saved ${user.email}`)
    } catch (e) {
      toast.error(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    setSaving(true)
    try {
      await deleteUserAccess(user.email)
      await onSaved()
      toast.success(`Removed ${user.email}`)
    } catch (e) {
      toast.error(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  // Only features that are globally on can be granted or revoked per person —
  // an off feature is off for everyone, so a per-user rule would be a lie.
  const grantable = features.filter((f) => f.enabled && !f.always_on)

  return (
    <div className="space-y-3 px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <User className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="truncate text-sm font-medium">{user.email}</span>
          {user.locked && (
            <Badge variant="outline" className="text-[10px]" title="Pinned by the APP_ADMIN_EMAILS environment variable">
              env admin
            </Badge>
          )}
          {overrideCount > 0 && (
            <Badge variant="outline" className="text-[10px]">
              {overrideCount} override{overrideCount === 1 ? '' : 's'}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Select value={role} onValueChange={(v) => setRole(v as UserAccessRecord['role'])} disabled={user.locked}>
            <SelectTrigger className="h-8 w-[130px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="member">Member</SelectItem>
              <SelectItem value="admin">Admin</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="ghost" size="sm" className="h-8 text-xs"
                  onClick={() => setExpanded((v) => !v)}>
            {expanded ? 'Hide' : 'Permissions'}
          </Button>
          <Button size="sm" className="h-8 gap-1.5 text-xs" disabled={!dirty || saving} onClick={save}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Save
          </Button>
          {!user.locked && (
            <Button variant="ghost" size="sm" className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                    disabled={saving} onClick={remove} title="Remove this row (falls back to the defaults)">
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="grid gap-2 rounded-md border bg-muted/30 p-3 sm:grid-cols-2">
          {grantable.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No features are switched on to grant.
            </p>
          )}
          {grantable.map((feature) => {
            const value = overrides[feature.key]
            const current = value === undefined || value === null ? 'inherit' : value ? 'allow' : 'deny'
            return (
              <div key={feature.key} className="flex items-center justify-between gap-2">
                <span className="truncate text-xs" title={feature.key}>{feature.label}</span>
                <Select
                  value={current}
                  onValueChange={(v) =>
                    setOverrides((prev) => ({
                      ...prev,
                      [feature.key]: v === 'inherit' ? null : v === 'allow',
                    }))
                  }
                >
                  <SelectTrigger className="h-7 w-[110px] text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="inherit">Default</SelectItem>
                    <SelectItem value="allow">Allow</SelectItem>
                    <SelectItem value="deny">Deny</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function cleaned(overrides: Record<string, OverrideValue>): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  for (const [key, value] of Object.entries(overrides)) {
    if (typeof value === 'boolean') out[key] = value
  }
  return out
}

function PeoplePanel() {
  const { users, isLoading, mutate } = useAdminUsers()
  const { features } = useAdminFeatures()
  const [newEmail, setNewEmail] = useState('')
  const [adding, setAdding] = useState(false)

  const add = async () => {
    const email = newEmail.trim().toLowerCase()
    if (!email.includes('@')) {
      toast.error('Enter a full login email')
      return
    }
    setAdding(true)
    try {
      await saveUserAccess({ email, role: 'member', overrides: {} })
      await mutate()
      setNewEmail('')
      toast.success(`Added ${email}`)
    } catch (e) {
      toast.error(errorMessage(e))
    } finally {
      setAdding(false)
    }
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-semibold">People</CardTitle>
        <CardDescription className="text-xs">
          Anyone signing in with a @bici.cc Google account gets member access to
          whatever is switched on. Add a row here only to make someone an admin,
          or to allow/deny specific features for them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 p-0">
        <div className="flex flex-wrap items-end gap-2 px-6">
          <div className="min-w-[240px] flex-1 space-y-1">
            <Label htmlFor="new-user-email" className="text-xs">Login email</Label>
            <Input
              id="new-user-email"
              placeholder="name@bici.cc"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void add() }}
              className="h-8 text-sm"
            />
          </div>
          <Button size="sm" className="h-8 gap-1.5 text-xs" disabled={adding} onClick={add}>
            {adding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
            Add
          </Button>
        </div>

        <div className="divide-y border-t">
          {isLoading && users.length === 0 && (
            <div className="px-6 py-4"><Skeleton className="h-8 w-full" /></div>
          )}
          {!isLoading && users.length === 0 && (
            <p className="px-6 py-6 text-sm text-muted-foreground">
              No per-user rules yet — everyone sees the switched-on features.
            </p>
          )}
          {users.map((user) => (
            <UserRow key={user.email} user={user} features={features}
                     onSaved={async () => { await mutate() }} />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------

export function AdminSettingsContent() {
  const { access, isAdmin, bootstrapMode, accessUnavailable } = useAccess()

  return (
    <div className="space-y-4 p-4 lg:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <ShieldCheck className="h-5 w-5" />
          Admin
        </h1>
        <p className="text-sm text-muted-foreground">
          Turn features on or off across the app, and set who can see what. A
          feature that is off is hidden everywhere and its endpoints are refused,
          so it uses no BigQuery, Lightspeed, or scheduler time.
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Signed in as{' '}
          <span className="font-medium text-foreground">{access?.email ?? 'unknown'}</span>
          {access && <> · role <span className="font-medium text-foreground">{access.role}</span></>}
        </p>
      </div>

      {accessUnavailable && (
        <div className="flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="text-sm">
            <p className="font-medium">Couldn’t reach the access settings API</p>
            <p className="text-muted-foreground">
              The backend may not have redeployed yet, or it can’t reach its
              database. Everything below will fail to load until it responds.
            </p>
          </div>
        </div>
      )}

      {bootstrapMode && isAdmin && (
        <div className="flex items-start gap-3 rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div className="text-sm">
            <p className="font-medium">No admin is configured yet</p>
            <p className="text-muted-foreground">
              Until one exists, everyone who signs in is treated as an admin and
              can change these settings. Add yourself under <strong>People</strong>{' '}
              and set your role to Admin — that ends this state and locks the page
              to real admins. (Setting <code>APP_ADMIN_EMAILS</code> on the backend
              does the same and survives a database reset.)
            </p>
          </div>
        </div>
      )}

      <Tabs defaultValue="features" className="w-full">
        <TabsList>
          <TabsTrigger value="features" className="gap-1.5">
            <Layers className="h-4 w-4" /> Features
          </TabsTrigger>
          <TabsTrigger value="people" className="gap-1.5">
            <User className="h-4 w-4" /> People
          </TabsTrigger>
        </TabsList>
        <TabsContent value="features" className="mt-4">
          <FeaturesPanel />
        </TabsContent>
        <TabsContent value="people" className="mt-4">
          <PeoplePanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}
