import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { useCatalog } from '../hooks/useCatalog'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card, StatCard } from '../components/ui/Card'
import { ErrorState } from '../components/ui/ErrorState'
import {
  DISPATCH_LABELS,
  ROLE_LABELS,
  STATUS_LABELS,
  type AccountIdentity,
  type IncidentSummary,
} from '../lib/api'

function relativeTime(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}

function AccountSummaryCard({ account }: { account: AccountIdentity | null }) {
  return (
    <Card as="section">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-ink-900 text-base font-semibold">Your account</h2>
        <Link to="/account" className="text-brand-700 text-sm font-medium hover:underline">
          Manage account
        </Link>
      </div>
      <dl className="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">
            Signed in as
          </dt>
          <dd className="text-ink-900 mt-1 text-sm break-all">{account?.email ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">Role</dt>
          <dd className="mt-1">
            <span className="bg-brand-50 text-brand-800 ring-brand-200 inline-block rounded-full px-2.5 py-0.5 text-sm font-medium ring-1 ring-inset">
              {account ? ROLE_LABELS[account.role] : '—'}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">Status</dt>
          <dd className="mt-1 flex items-center gap-2 text-sm">
            <span
              aria-hidden="true"
              className={`h-2 w-2 rounded-full ${
                account?.is_active ? 'bg-emerald-500' : 'bg-ink-300'
              }`}
            />
            <span className="text-ink-900">
              {account?.is_active ? 'Verified and active' : 'Inactive'}
            </span>
          </dd>
        </div>
      </dl>

      <p className="text-ink-500 mt-4 text-xs leading-relaxed">
        Your role is set by your institution and read from the CampusShield database on every
        request. It is not something this browser can change.
        {account?.role === 'student' && ' Student names are not stored.'}
      </p>
    </Card>
  )
}

function StudentDashboard() {
  const { locations, categories, loading, error, reload } = useCatalog()

  const incidentCount = categories?.filter((c) => c.kind === 'incident').length ?? 0
  const concernCount = categories?.filter((c) => c.kind === 'concern').length ?? 0

  return (
    <>
      <div>
        <h1 className="text-ink-900 text-2xl font-semibold tracking-tight sm:text-3xl">
          Your overview
        </h1>
        <p className="text-ink-600 mt-1.5 text-sm">
          Report safely. Help improve campus safety.
        </p>
      </div>

      {error != null && <ErrorState error={error} onRetry={reload} />}

      {/* Primary student action */}
      <Card as="section" className="border-brand-200 bg-brand-50/60 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-ink-900 text-base font-semibold">
              Something happened, or something feels unsafe?
            </h2>
            <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
              You can report it with your account, or anonymously. It takes a couple of
              minutes.
            </p>
          </div>
          <div className="flex shrink-0 gap-3">
            <Link to="/report">
              <Button size="lg">Report a safety concern</Button>
            </Link>
          </div>
        </div>
      </Card>

      {/* Campus data */}
      <section aria-labelledby="campus-data">
        <h2 id="campus-data" className="text-ink-900 mb-3 text-base font-semibold">
          Campus reporting data
        </h2>
        <dl className="grid gap-4 sm:grid-cols-3">
          <StatCard
            label="Campus locations"
            value={locations?.length ?? 0}
            loading={loading}
            detail={
              locations && locations.length === 0
                ? 'None published yet — locations appear once their coordinates are verified.'
                : 'Available to select when reporting.'
            }
          />
          <StatCard
            label="Incident types"
            value={incidentCount}
            loading={loading}
            detail="Things that happened to you or that you witnessed."
          />
          <StatCard
            label="Safety concerns"
            value={concernCount}
            loading={loading}
            detail="Hazards like poor lighting or an isolated route."
          />
        </dl>
      </section>

      {!loading && locations?.length === 0 && (
        <Alert tone="info" title="Campus locations are not published yet">
          <p>
            Reporting opens once verified locations for the Rajanukunte campus have been added.
            Every report is anchored to a specific place so that patterns can be spotted and
            acted on, which means a location must have confirmed coordinates before it can be
            offered.
          </p>
        </Alert>
      )}

      <Card as="section" className="border-ink-200 bg-ink-50/60 p-5 sm:p-6">
        <h2 className="text-ink-900 text-base font-semibold">Coming next</h2>
        <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
          A campus safety map and the tools the university uses to spot repeated problems are
          still being built. Reporting works now.
        </p>
      </Card>
    </>
  )
}

function StaffDashboard({ role }: { role: 'security' | 'icc' | 'admin' }) {
  const { api } = useAuth()
  const [incidents, setIncidents] = useState<IncidentSummary[] | null>(null)
  const [total, setTotal] = useState<number>(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [reloadNonce, setReloadNonce] = useState(0)

  const reload = useCallback(() => {
    setLoading(true)
    setReloadNonce((n) => n + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    void api
      .incidents(100, 0)
      .then((page) => {
        if (cancelled) return
        setIncidents(page.items)
        setTotal(page.pagination.total)
        setError(null)
        setLoading(false)
      })
      .catch((cause) => {
        if (cancelled) return
        setError(cause)
        setIncidents([])
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [api, reloadNonce])

  const roleMeta = useMemo(() => {
    switch (role) {
      case 'security':
        return {
          title: 'Security Overview',
          subtitle: 'Monitor, triage, and respond to campus safety incidents.',
          stat1: {
            label: 'Active incidents',
            value: total,
            detail: 'Total open incidents routed to campus security.',
          },
          stat2: {
            label: 'Emergency alerts',
            value: incidents?.filter((i) => i.is_emergency).length ?? 0,
            detail: 'Immediate emergencies requiring active monitoring.',
          },
          stat3: {
            label: 'Pending dispatch',
            value:
              incidents?.filter(
                (i) => i.dispatch?.state === 'pending' || (i.is_emergency && !i.dispatch),
              ).length ?? 0,
            detail: 'Incidents requiring immediate dispatch or acknowledgement.',
          },
          stat4: {
            label: 'Assigned cases',
            value: incidents?.filter((i) => i.is_assigned).length ?? 0,
            detail: 'Incidents currently claimed by response staff.',
          },
        }
      case 'icc':
        return {
          title: 'ICC Overview',
          subtitle:
            'Review and manage confidential cases routed to the Internal Complaints Committee.',
          stat1: {
            label: 'Active cases',
            value: total,
            detail: 'Total confidential cases under committee jurisdiction.',
          },
          stat2: {
            label: 'Under review',
            value: incidents?.filter((i) => i.status === 'under_review').length ?? 0,
            detail: 'Cases actively undergoing formal inquiry or review.',
          },
          stat3: {
            label: 'Assigned cases',
            value: incidents?.filter((i) => i.is_assigned).length ?? 0,
            detail: 'Cases assigned to an ICC committee member.',
          },
          stat4: {
            label: 'Action taken',
            value: incidents?.filter((i) => i.status === 'action_taken').length ?? 0,
            detail: 'Cases with initial protective or administrative actions.',
          },
        }
      case 'admin':
      default:
        return {
          title: 'Admin Overview',
          subtitle: 'Campus safety operations, incident metrics, and operational oversight.',
          stat1: {
            label: 'Active incidents',
            value: total,
            detail: 'Total active cases across campus jurisdiction.',
          },
          stat2: {
            label: 'Emergency incidents',
            value: incidents?.filter((i) => i.is_emergency).length ?? 0,
            detail: 'Active emergency alerts across campus.',
          },
          stat3: {
            label: 'Cases under review',
            value: incidents?.filter((i) => i.status === 'under_review').length ?? 0,
            detail: 'Cases currently in committee or investigation review.',
          },
          stat4: {
            label: 'Triaged cases',
            value: incidents?.filter((i) => i.status === 'triaged').length ?? 0,
            detail: 'Incidents triaged and prepared for operational handling.',
          },
        }
    }
  }, [role, total, incidents])

  const recentIncidents = useMemo(() => {
    return (incidents ?? []).slice(0, 5)
  }, [incidents])

  return (
    <>
      <div>
        <h1 className="text-ink-900 text-2xl font-semibold tracking-tight sm:text-3xl">
          {roleMeta.title}
        </h1>
        <p className="text-ink-600 mt-1.5 text-sm">{roleMeta.subtitle}</p>
      </div>

      {error != null && <ErrorState error={error} onRetry={reload} />}

      {/* Primary operational action banner */}
      <Card as="section" className="border-brand-200 bg-brand-50/60 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-ink-900 text-base font-semibold">Incident Queue & Action</h2>
            <p className="text-ink-600 mt-1 text-sm leading-relaxed">
              Review live incident reports, inspect mapped campus locations, manage dispatches,
              and update case lifecycles.
            </p>
          </div>
          <div className="flex shrink-0 gap-3">
            <Link to="/incidents">
              <Button size="lg">Take action toward incidents</Button>
            </Link>
          </div>
        </div>
      </Card>

      {/* Operational Metrics Cards */}
      <section aria-labelledby="operational-metrics">
        <h2 id="operational-metrics" className="text-ink-900 mb-3 text-base font-semibold">
          Operational metrics
        </h2>
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label={roleMeta.stat1.label}
            value={roleMeta.stat1.value}
            loading={loading}
            detail={roleMeta.stat1.detail}
          />
          <StatCard
            label={roleMeta.stat2.label}
            value={roleMeta.stat2.value}
            loading={loading}
            detail={roleMeta.stat2.detail}
          />
          <StatCard
            label={roleMeta.stat3.label}
            value={roleMeta.stat3.value}
            loading={loading}
            detail={roleMeta.stat3.detail}
          />
          <StatCard
            label={roleMeta.stat4.label}
            value={roleMeta.stat4.value}
            loading={loading}
            detail={roleMeta.stat4.detail}
          />
        </dl>
      </section>

      {/* Recent & Priority Incidents Preview */}
      <section aria-labelledby="recent-incidents">
        <div className="mb-3 flex items-center justify-between">
          <h2 id="recent-incidents" className="text-ink-900 text-base font-semibold">
            Recent / Priority incidents
          </h2>
          <Link to="/incidents" className="text-brand-700 text-sm font-medium hover:underline">
            View full queue →
          </Link>
        </div>

        {loading ? (
          <div className="text-ink-500 py-8 text-center text-sm">Loading open incidents…</div>
        ) : recentIncidents.length === 0 ? (
          <Card className="p-6 text-center">
            <p className="text-ink-700 text-sm font-medium">No open incidents in your queue</p>
            <p className="text-ink-500 mt-1 text-xs">
              All routed incidents have been resolved or closed.
            </p>
          </Card>
        ) : (
          <div className="divide-ink-200 border-ink-200 overflow-hidden rounded-xl border bg-white divide-y">
            {recentIncidents.map((incident) => (
              <div
                key={incident.public_ref}
                className="hover:bg-ink-50/50 flex flex-col justify-between gap-3 p-4 transition-colors sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-ink-900 font-mono text-xs font-semibold">
                      #{incident.public_ref}
                    </span>
                    <span className="bg-ink-100 text-ink-700 rounded-full px-2 py-0.5 text-xs font-medium">
                      {incident.category?.label ?? 'Uncategorised'}
                    </span>
                    <span className="bg-brand-50 text-brand-800 rounded-full px-2 py-0.5 text-xs font-medium">
                      {STATUS_LABELS[incident.status]}
                    </span>
                    {incident.is_emergency && (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">
                        Emergency
                      </span>
                    )}
                    {incident.dispatch && (
                      <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-800">
                        {DISPATCH_LABELS[incident.dispatch.state]}
                      </span>
                    )}
                  </div>
                  <div className="text-ink-500 mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                    <span>📍 {incident.location.name}</span>
                    <span>🕒 {relativeTime(incident.submitted_at)}</span>
                    <span>{incident.is_assigned ? '👤 Assigned' : '⚪ Unassigned'}</span>
                  </div>
                </div>
                <div className="shrink-0">
                  <Link to="/incidents">
                    <Button variant="secondary" size="sm">
                      View incident
                    </Button>
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  )
}

export function DashboardPage() {
  const { account } = useAuth()
  const isStudent = !account || account.role === 'student'

  return (
    <AppShell>
      <div className="space-y-6">
        {isStudent ? (
          <StudentDashboard />
        ) : (
          <StaffDashboard role={account.role as 'security' | 'icc' | 'admin'} />
        )}

        <AccountSummaryCard account={account} />
      </div>
    </AppShell>
  )
}
