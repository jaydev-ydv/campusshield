import { useEffect, useState } from 'react'

import { useAuth } from '../auth/useAuth'
import { Alert } from '../components/ui/Alert'
import { Spinner } from '../components/ui/Spinner'
import { ApiError } from '../lib/apiClient'

/**
 * The image attached to an incident, shown to an authorised responder.
 *
 * ## Why this fetches instead of using an `<img src>`
 *
 * There is no URL that would work. Evidence is served by an endpoint that
 * re-checks authorisation on every request and streams the bytes; it mints no
 * signed URL and no public one, so there is nothing to put in a `src`. The
 * image is fetched with the bearer token, held as a Blob, and rendered from an
 * object URL that exists only in this tab and is revoked on unmount.
 *
 * That is a deliberate cost. A signed URL would be simpler and would also keep
 * working for its whole lifetime for anyone it was forwarded to — including
 * evidence attached to an anonymous report.
 *
 * ## What the responder is told about the image
 *
 * That hidden location and device data were removed, and that what is visible
 * in the picture was not touched. And that a photograph supports an account; it
 * does not prove one.
 */
export function EvidenceViewer({
  evidenceIds,
  publicRef,
}: {
  evidenceIds: string[]
  publicRef: string
}) {
  if (evidenceIds.length === 0) {
    return <p className="text-ink-600 text-sm">No photographs were attached to this report.</p>
  }

  return (
    <div className="space-y-3">
      <p className="text-ink-600 text-sm leading-relaxed">
        {evidenceIds.length === 1
          ? 'One photograph was'
          : `${evidenceIds.length} photographs were`}{' '}
        attached by the reporter. Hidden location and device data have been removed. A
        photograph supports an account of what happened; it does not on its own establish it.
      </p>
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {evidenceIds.map((evidenceId, index) => (
          <li key={evidenceId}>
            <EvidenceImage
              evidenceId={evidenceId}
              label={`Photograph ${index + 1} of ${evidenceIds.length} for incident ${publicRef}`}
            />
          </li>
        ))}
      </ul>
    </div>
  )
}

function EvidenceImage({ evidenceId, label }: { evidenceId: string; label: string }) {
  const { api } = useAuth()
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let revoked = false
    let created: string | null = null

    api
      .evidenceImage(evidenceId)
      .then((blob) => {
        if (revoked) return
        created = URL.createObjectURL(blob)
        setObjectUrl(created)
      })
      .catch((cause: unknown) => {
        if (revoked) return
        setError(cause instanceof ApiError ? cause.message : 'The image could not be loaded.')
      })

    return () => {
      revoked = true
      // The object URL is the only handle to these bytes. Revoking it on unmount
      // keeps evidence out of memory once the responder has moved on.
      if (created) URL.revokeObjectURL(created)
    }
  }, [api, evidenceId])

  if (error) {
    return (
      <Alert tone="warning" title="Image unavailable">
        <p>{error}</p>
      </Alert>
    )
  }

  if (!objectUrl) {
    return (
      <div className="border-ink-200 bg-ink-50 flex aspect-video items-center justify-center rounded-lg border">
        <Spinner size="md" />
        <span className="sr-only">Loading {label}</span>
      </div>
    )
  }

  return (
    <figure className="border-ink-200 overflow-hidden rounded-lg border bg-white">
      <img src={objectUrl} alt={label} className="max-h-96 w-full bg-black object-contain" />
      <figcaption className="text-ink-500 px-3 py-2 text-xs">
        Metadata removed before storage. Visible content is unchanged.
      </figcaption>
    </figure>
  )
}
