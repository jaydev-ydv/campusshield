/**
 * A best-effort browser position for the emergency ("SOS") trigger.
 *
 * The one rule this exists to enforce: **a position is never a prerequisite.**
 * `getEmergencyPosition` never rejects — permission denied, no fix in time, no
 * `navigator.geolocation` at all, all resolve to `null` rather than throwing,
 * so a caller can `await` this and always have *something* to send, whether
 * that is a coordinate or nothing.
 */

export interface EmergencyPosition {
  latitude: number
  longitude: number
  accuracy?: number
  captured_at?: string
}

export type LocationCaptureStatus =
  'idle' | 'acquiring' | 'acquired' | 'denied' | 'unavailable' | 'timeout' | 'unsupported'

/** Generous but bounded: long enough for a real GPS fix indoors, short enough
 *  that a slow or absent signal never meaningfully delays the alert — see
 *  `SosButton`, which starts this the moment the hold gesture begins so the
 *  read has the whole hold duration to resolve before it would matter. */
export const EMERGENCY_POSITION_TIMEOUT_MS = 5000

export function getEmergencyPosition(
  timeoutMs: number = EMERGENCY_POSITION_TIMEOUT_MS,
  onStatusChange?: (
    status: LocationCaptureStatus,
    position?: EmergencyPosition | null,
  ) => void,
): Promise<EmergencyPosition | null> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    onStatusChange?.('unsupported', null)
    return Promise.resolve(null)
  }

  onStatusChange?.('acquiring')

  return new Promise((resolve) => {
    let settled = false
    const done = (position: EmergencyPosition | null, status: LocationCaptureStatus) => {
      if (settled) return
      settled = true
      onStatusChange?.(status, position)
      resolve(position)
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const accuracy = Math.round(pos.coords.accuracy * 10) / 10
        const capturedAt = new Date(pos.timestamp || Date.now()).toISOString()
        done(
          {
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
            accuracy,
            captured_at: capturedAt,
          },
          'acquired',
        )
      },
      (error) => {
        let status: LocationCaptureStatus = 'unavailable'
        if (error.code === error.PERMISSION_DENIED) {
          status = 'denied'
        } else if (error.code === error.TIMEOUT) {
          status = 'timeout'
        }
        done(null, status)
      },
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 0 },
    )
  })
}

/** Check browser geolocation permission state without triggering a prompt */
export async function checkGeolocationPermission(): Promise<
  'granted' | 'prompt' | 'denied' | 'unsupported'
> {
  if (typeof navigator === 'undefined' || !('permissions' in navigator)) {
    return 'unsupported'
  }
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' as PermissionName })
    return status.state
  } catch {
    return 'unsupported'
  }
}
