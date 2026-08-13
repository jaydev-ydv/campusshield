import { useCallback, useId, useRef, useState } from 'react'

import { useAuth } from '../auth/useAuth'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Spinner } from '../components/ui/Spinner'
import { ApiError } from '../lib/apiClient'
import type { EvidenceLimits } from '../lib/api'

/**
 * One image the student has added, in whatever state it is in.
 *
 * `previewUrl` is a local object URL — the image is never fetched back from the
 * server for preview, so the browser shows the file the student chose rather
 * than the sanitised copy. That difference is worth knowing about: the stored
 * image has had its metadata stripped, and the preview has not.
 */
export interface EvidenceItem {
  localId: string
  fileName: string
  byteSize: number
  previewUrl: string
  status: 'uploading' | 'uploaded' | 'failed'
  /** The server's capability token. Present only once uploaded. */
  token?: string
  error?: string
}

interface EvidenceUploadProps {
  items: EvidenceItem[]
  /**
   * The parent's `useState` setter, passed directly.
   *
   * A plain `onChange(items)` callback is not enough here: several uploads can
   * be in flight at once, and each resolves against whatever the list was when
   * it started. Functional updates are the only way each result lands on the
   * current list rather than on a stale snapshot — with a value callback, two
   * files chosen together lose one.
   */
  setItems: React.Dispatch<React.SetStateAction<EvidenceItem[]>>
  limits: EvidenceLimits | null
  disabled?: boolean
}

const FALLBACK_LIMITS: EvidenceLimits = {
  max_bytes: 10 * 1024 * 1024,
  accepted_types: ['image/jpeg', 'image/png', 'image/webp'],
  max_per_report: 5,
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function EvidenceUpload({ items, setItems, limits, disabled }: EvidenceUploadProps) {
  const { api } = useAuth()
  const inputId = useId()
  const fileInput = useRef<HTMLInputElement>(null)
  const [announcement, setAnnouncement] = useState('')

  const active = limits ?? FALLBACK_LIMITS
  const atLimit = items.length >= active.max_per_report

  const upload = useCallback(
    async (file: File) => {
      const localId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const previewUrl = URL.createObjectURL(file)

      setItems((previous) => [
        ...previous,
        {
          localId,
          fileName: file.name,
          byteSize: file.size,
          previewUrl,
          status: 'uploading' as const,
        },
      ])

      try {
        const result = await api.uploadEvidence(file)
        setItems((previous) =>
          previous.map((item) =>
            item.localId === localId
              ? { ...item, status: 'uploaded' as const, token: result.upload_token }
              : item,
          ),
        )
        setAnnouncement(`${file.name} added.`)
      } catch (error) {
        const message =
          error instanceof ApiError
            ? error.message
            : 'That image could not be added. Please try again.'
        setItems((previous) =>
          previous.map((item) =>
            item.localId === localId
              ? { ...item, status: 'failed' as const, error: message }
              : item,
          ),
        )
        setAnnouncement(`${file.name} could not be added.`)
      }
    },
    [api, setItems],
  )

  const reject = useCallback(
    (file: File, error: string) => {
      setItems((previous) => [
        ...previous,
        {
          localId: `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2, 6)}`,
          fileName: file.name,
          byteSize: file.size,
          previewUrl: '',
          status: 'failed' as const,
          error,
        },
      ])
      setAnnouncement(`${file.name} could not be added.`)
    },
    [setItems],
  )

  const handleFiles = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList) return
      const room = Math.max(active.max_per_report - items.length, 0)

      for (const file of Array.from(fileList).slice(0, room)) {
        // The server checks the bytes regardless; these only save a round trip
        // and give immediate feedback.
        if (file.type !== '' && !active.accepted_types.includes(file.type)) {
          reject(file, 'Only JPEG, PNG and WebP images can be added.')
          continue
        }
        if (file.size > active.max_bytes) {
          reject(file, `This image is larger than ${formatBytes(active.max_bytes)}.`)
          continue
        }
        await upload(file)
      }
      if (fileInput.current) fileInput.current.value = ''
    },
    [active, items.length, reject, upload],
  )

  const remove = useCallback(
    async (item: EvidenceItem) => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
      setItems((previous) => previous.filter((i) => i.localId !== item.localId))
      setAnnouncement(`${item.fileName} removed.`)

      // Tell the server too, so the staged object goes now rather than waiting
      // hours for the reaper.
      if (item.token) {
        try {
          await api.discardEvidence(item.token)
        } catch {
          // The reaper will collect it. Not worth an error for something the
          // student has already seen disappear.
        }
      }
    },
    [api, setItems],
  )

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-ink-900 text-base font-medium">Add a photo (optional)</h2>
        <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
          A photo can help whoever responds understand the situation. You do not need one, and
          a report without a photo is treated exactly the same.
        </p>
      </div>

      {/* The privacy point, stated before the picker rather than after. */}
      <Alert tone="info" title="Before you add a photo">
        <p>
          We automatically remove hidden location and device information from images you add.
        </p>
        <p className="mt-2">
          <strong>We cannot change what is visible in the picture itself.</strong> Faces, name
          badges, vehicle number plates, documents, or a reflection in a window will stay in
          the image. Only add evidence you are comfortable sharing with authorised responders.
        </p>
      </Alert>

      {!atLimit && (
        <div>
          <input
            ref={fileInput}
            id={inputId}
            type="file"
            accept={active.accepted_types.join(',')}
            multiple
            // On a phone this offers the camera alongside the gallery. Without
            // it the student can only pick an existing photo.
            capture={undefined}
            disabled={disabled}
            onChange={(e) => void handleFiles(e.target.files)}
            className="sr-only"
          />
          <label
            htmlFor={inputId}
            className={[
              'border-ink-300 hover:border-brand-400 hover:bg-brand-50/40 flex cursor-pointer',
              'flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed',
              'px-4 py-8 text-center transition-colors',
              disabled ? 'cursor-not-allowed opacity-60' : '',
            ].join(' ')}
          >
            <svg
              viewBox="0 0 24 24"
              className="text-ink-400 h-8 w-8"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M3 7h3l2-2h8l2 2h3v12H3z" />
              <circle cx="12" cy="13" r="3.5" />
            </svg>
            <span className="text-ink-800 text-sm font-medium">Take or choose a photo</span>
            <span className="text-ink-500 text-xs">
              JPEG, PNG or WebP · up to {formatBytes(active.max_bytes)} ·{' '}
              {active.max_per_report - items.length} remaining
            </span>
          </label>
        </div>
      )}

      {atLimit && (
        <p className="text-ink-600 text-sm">
          You have added the maximum of {active.max_per_report} photos. Remove one to add
          another.
        </p>
      )}

      {/* Announced to assistive technology; upload results are otherwise silent. */}
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {items.length > 0 && (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {items.map((item) => (
            <li key={item.localId}>
              <div
                className={[
                  'border-ink-200 relative overflow-hidden rounded-lg border bg-white',
                  item.status === 'failed' ? 'border-red-300' : '',
                ].join(' ')}
              >
                <div className="bg-ink-100 flex aspect-square items-center justify-center">
                  {item.previewUrl ? (
                    <img
                      src={item.previewUrl}
                      alt={`Preview of ${item.fileName}`}
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <span className="text-ink-400 px-2 text-center text-xs">No preview</span>
                  )}
                  {item.status === 'uploading' && (
                    <div className="bg-ink-900/40 absolute inset-0 flex items-center justify-center">
                      <Spinner size="md" className="text-white" />
                      <span className="sr-only">Adding {item.fileName}</span>
                    </div>
                  )}
                </div>

                <div className="p-2">
                  <p className="text-ink-700 truncate text-xs" title={item.fileName}>
                    {item.fileName}
                  </p>
                  {item.status === 'failed' ? (
                    <p role="alert" className="mt-0.5 text-xs text-red-700">
                      {item.error}
                    </p>
                  ) : (
                    <p className="text-ink-500 text-xs">
                      {item.status === 'uploading' ? 'Adding…' : formatBytes(item.byteSize)}
                    </p>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-1 w-full"
                    onClick={() => void remove(item)}
                    disabled={disabled}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
