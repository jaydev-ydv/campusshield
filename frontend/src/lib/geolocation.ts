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
}

/** Generous but bounded: long enough for a real GPS fix indoors, short enough
 *  that a slow or absent signal never meaningfully delays the alert — see
 *  `SosButton`, which starts this the moment the hold gesture begins so the
 *  read has the whole hold duration to resolve before it would matter. */
export const EMERGENCY_POSITION_TIMEOUT_MS = 5000

export function getEmergencyPosition(
  timeoutMs: number = EMERGENCY_POSITION_TIMEOUT_MS,
): Promise<EmergencyPosition | null> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    return Promise.resolve(null)
  }

  return new Promise((resolve) => {
    let settled = false
    const done = (position: EmergencyPosition | null) => {
      if (settled) return
      settled = true
      resolve(position)
    }

    navigator.geolocation.getCurrentPosition(
      (position) =>
        done({ latitude: position.coords.latitude, longitude: position.coords.longitude }),
      // Permission denied, position unavailable, or the browser's own
      // timeout — every failure mode collapses to the same "no position",
      // since none of them should ever block the alert.
      () => done(null),
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 0 },
    )
  })
}
