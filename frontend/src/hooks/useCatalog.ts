import { useCallback, useEffect, useState } from 'react'

import { useAuth } from '../auth/useAuth'
import type { CampusLocation, ReportCategory } from '../lib/api'

interface CatalogResult {
  /** Which fetch produced this, so staleness is derivable rather than tracked. */
  nonce: number
  locations: CampusLocation[] | null
  categories: ReportCategory[] | null
  error: unknown
}

interface CatalogState {
  locations: CampusLocation[] | null
  categories: ReportCategory[] | null
  loading: boolean
  error: unknown
  reload: () => void
}

/**
 * Loads the campus location and report category vocabularies.
 *
 * Both are fetched together because the dashboard shows them together, and two
 * independent loading states for one panel produces a visibly jittery layout.
 *
 * `Promise.allSettled` rather than `all`: one failing endpoint must not blank
 * the other. Locations legitimately being empty is not a reason to hide the
 * category counts.
 */
export function useCatalog(): CatalogState {
  const { api, status } = useAuth()
  const [nonce, setNonce] = useState(0)
  const [result, setResult] = useState<CatalogResult | null>(null)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (status !== 'authenticated') return

    let cancelled = false
    void Promise.allSettled([api.locations(), api.categories()]).then(
      ([locationResult, categoryResult]) => {
        // The component may have unmounted, or the user signed out, while these
        // were in flight. Writing state then would resurrect a dead screen.
        if (cancelled) return

        setResult({
          nonce,
          locations: locationResult.status === 'fulfilled' ? locationResult.value : null,
          categories: categoryResult.status === 'fulfilled' ? categoryResult.value : null,
          error:
            locationResult.status === 'rejected'
              ? locationResult.reason
              : categoryResult.status === 'rejected'
                ? categoryResult.reason
                : null,
        })
      },
    )

    return () => {
      cancelled = true
    }
  }, [api, status, nonce])

  // Derived, not stored. Setting a loading flag synchronously inside the effect
  // triggers a cascading render; comparing the nonce answers the same question
  // from state that already exists.
  const loading = status === 'authenticated' && result?.nonce !== nonce

  return {
    locations: result?.locations ?? null,
    categories: result?.categories ?? null,
    loading,
    error: result?.error ?? null,
    reload,
  }
}
