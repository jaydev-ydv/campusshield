import { Link } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { useCatalog } from '../hooks/useCatalog'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card, StatCard } from '../components/ui/Card'
import { ErrorState } from '../components/ui/ErrorState'
import { ROLE_LABELS } from '../lib/api'

export function DashboardPage() {
  const { account } = useAuth()
  const { locations, categories, loading, error, reload } = useCatalog()

  const incidentCount = categories?.filter((c) => c.kind === 'incident').length ?? 0
  const concernCount = categories?.filter((c) => c.kind === 'concern').length ?? 0

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-ink-900 text-2xl font-semibold tracking-tight sm:text-3xl">
            Your overview
          </h1>
          <p className="text-ink-600 mt-1.5 text-sm">
            Report safely. Help improve campus safety.
          </p>
        </div>

        {error != null && <ErrorState error={error} onRetry={reload} />}

        {/* The primary action. Placed first because someone opening CampusShield
            has usually come to do exactly this, and making them hunt for it adds
            friction at the worst moment. */}
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

        {/* Account -------------------------------------------------------- */}
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
              <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">
                Role
              </dt>
              <dd className="mt-1">
                <span className="bg-brand-50 text-brand-800 ring-brand-200 inline-block rounded-full px-2.5 py-0.5 text-sm font-medium ring-1 ring-inset">
                  {account ? ROLE_LABELS[account.role] : '—'}
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">
                Status
              </dt>
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
            Your role is set by your institution and read from the CampusShield database on
            every request. It is not something this browser can change.
            {account?.role === 'student' && ' Student names are not stored.'}
          </p>
        </Card>

        {/* Campus data ---------------------------------------------------- */}
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

        {/* The empty-location case is a real state, not a fault ----------- */}
        {!loading && locations?.length === 0 && (
          <Alert tone="info" title="Campus locations are not published yet">
            <p>
              Reporting opens once verified locations for the Rajanukunte campus have been
              added. Every report is anchored to a specific place so that patterns can be
              spotted and acted on, which means a location must have confirmed coordinates
              before it can be offered.
            </p>
          </Alert>
        )}

        {/* Honest about what is not built yet ----------------------------- */}
        <Card as="section" className="bg-ink-50/60 border-ink-200 p-5 sm:p-6">
          <h2 className="text-ink-900 text-base font-semibold">Coming next</h2>
          <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
            A campus safety map and the tools the university uses to spot repeated problems are
            still being built. Reporting works now.
          </p>
        </Card>
      </div>
    </AppShell>
  )
}
