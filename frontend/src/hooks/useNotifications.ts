import { useCallback, useEffect, useState } from 'react'

import { useAuth } from '../auth/useAuth'

const POLL_INTERVAL_MS = 45_000

/**
 * The caller's unread notification count, refreshed on an interval.
 *
 * Polling, not a push subscription — no Firebase Cloud Messaging is configured
 * in this deployment, so a periodic `GET /notifications` is the whole delivery
 * mechanism there is. `limit=1` keeps each poll cheap; only `unread_count`,
 * computed server-side over the full set, is read from the response.
 */
export function useUnreadCount(): { unreadCount: number; refresh: () => void } {
  const { api, status } = useAuth()
  const [unreadCount, setUnreadCount] = useState(0)
  const [nonce, setNonce] = useState(0)

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (status !== 'authenticated') return

    let cancelled = false

    function poll() {
      void api
        .notifications(1, 0)
        .then((result) => {
          if (!cancelled) setUnreadCount(result.unread_count)
        })
        .catch(() => {
          // A failed poll leaves the last-known count on screen rather than
          // resetting to zero — a transient network blip should not read as
          // "everything is read."
        })
    }

    poll()
    const timer = window.setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [api, status, nonce])

  return { unreadCount: status === 'authenticated' ? unreadCount : 0, refresh }
}
