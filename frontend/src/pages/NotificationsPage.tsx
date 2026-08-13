import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/layout/AppShell'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { EmptyState, ErrorState } from '../components/ui/ErrorState'
import { LoadingState } from '../components/ui/Spinner'
import {
  NOTIFICATION_CATEGORY_LABELS,
  type AppNotification,
  type NotificationsResponse,
} from '../lib/api'

/**
 * The caller's own notification inbox.
 *
 * Backed by `GET /notifications`, scoped server-side to the authenticated
 * caller — there is no way to view anyone else's. Read status changes call
 * `POST /notifications/<id>/read` and then re-fetch, rather than updating
 * local state optimistically: the unread count in the nav bell is polled
 * independently, and re-fetching keeps this list the same authority for
 * both.
 */
export function NotificationsPage() {
  const { api, status } = useAuth()
  const [data, setData] = useState<NotificationsResponse | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [nonce, setNonce] = useState(0)
  const [markingId, setMarkingId] = useState<string | null>(null)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (status !== 'authenticated') return
    let cancelled = false

    void api
      .notifications()
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err)
      })

    return () => {
      cancelled = true
    }
  }, [api, status, nonce])

  async function handleMarkRead(notification: AppNotification) {
    if (notification.read_at) return
    setMarkingId(notification.notification_id)
    try {
      await api.markNotificationRead(notification.notification_id)
      reload()
    } catch (err) {
      setError(err)
    } finally {
      setMarkingId(null)
    }
  }

  const loading = data === null && error == null

  return (
    <AppShell>
      <div className="mx-auto max-w-2xl space-y-6">
        <div>
          <h1 className="text-ink-900 text-2xl font-semibold tracking-tight sm:text-3xl">
            Notifications
          </h1>
          <p className="text-ink-600 mt-1.5 text-sm">
            Updates on your reports and, if you respond to cases, on assignments.
          </p>
        </div>

        {error != null && <ErrorState error={error} onRetry={reload} />}

        {loading && <LoadingState label="Loading your notifications…" />}

        {data && data.items.length === 0 && (
          <EmptyState title="No notifications yet">
            You will see updates here when a report you submitted with your account changes
            status, or when a case is assigned to you.
          </EmptyState>
        )}

        {data && data.items.length > 0 && (
          <ul className="space-y-3">
            {data.items.map((notification) => (
              <li key={notification.notification_id}>
                <NotificationRow
                  notification={notification}
                  marking={markingId === notification.notification_id}
                  onMarkRead={() => void handleMarkRead(notification)}
                />
              </li>
            ))}
          </ul>
        )}

        <div className="text-center">
          <Link to="/dashboard" className="text-brand-700 text-sm font-medium hover:underline">
            Back to overview
          </Link>
        </div>
      </div>
    </AppShell>
  )
}

function NotificationRow({
  notification,
  marking,
  onMarkRead,
}: {
  notification: AppNotification
  marking: boolean
  onMarkRead: () => void
}) {
  const unread = notification.read_at === null
  const created = new Date(notification.created_at)

  return (
    <Card
      as="article"
      className={['p-4 sm:p-5', unread ? 'border-brand-200 bg-brand-50/40' : ''].join(' ')}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {unread && (
              <span
                aria-hidden="true"
                className="bg-brand-600 h-2 w-2 shrink-0 rounded-full"
              />
            )}
            <span className="text-ink-500 text-xs font-medium tracking-wide uppercase">
              {NOTIFICATION_CATEGORY_LABELS[notification.category]}
            </span>
          </div>
          <p className="text-ink-900 mt-1 text-sm font-semibold">{notification.title}</p>
          <p className="text-ink-700 mt-1 text-sm leading-relaxed">{notification.body}</p>
          <div className="text-ink-500 mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span>
              {created.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
            </span>
            {notification.related_public_ref &&
              // `status_update` always addresses the reporter, who owns the
              // report view at `/reports/<ref>`. `assignment` addresses a
              // responder, who works cases from the queue — there is no
              // deep link into one specific case yet, so this points at the
              // queue rather than a URL that would be wrong for them.
              (notification.category === 'status_update' ? (
                <Link
                  to={`/reports/${notification.related_public_ref}`}
                  className="text-brand-700 font-mono hover:underline"
                >
                  {notification.related_public_ref}
                </Link>
              ) : (
                <Link
                  to="/incidents"
                  className="text-brand-700 font-mono hover:underline"
                  title="Find it in your incident queue"
                >
                  {notification.related_public_ref}
                </Link>
              ))}
          </div>
        </div>
        {unread && (
          <Button variant="secondary" size="sm" onClick={onMarkRead} loading={marking}>
            Mark read
          </Button>
        )}
      </div>
    </Card>
  )
}
