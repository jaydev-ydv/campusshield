import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { ApiError } from '../lib/apiClient'
import { getEmergencyPosition } from '../lib/geolocation'

/**
 * The emergency ("SOS") trigger, visible on every authenticated page.
 *
 * ## Why press-and-hold, not a confirmation dialog
 *
 * A single unconfirmed tap risks an accidental alert — a pocket press, a
 * mis-click — which wastes a responder's attention and erodes trust in the
 * channel for the next, real one. The obvious fix is a confirmation dialog,
 * but this codebase has no modal pattern anywhere (the closest precedent,
 * `CaseLifecyclePanel`, confirms in place rather than in an overlay), and a
 * dialog adds exactly the wrong thing here: a second decision, a small
 * target to hit, and reading, at a moment that may have none of those to
 * spare.
 *
 * Press-and-hold asks for one thing instead: sustained, deliberate contact.
 * It cannot be triggered by a stray tap or a bag brushing a screen. It needs
 * no reading — the fill and the countdown are confirmation enough. And it
 * behaves identically across input methods without extra code: a mouse or
 * touch hold is `pointerdown`→wait→`pointerup`, and a keyboard hold is the
 * same shape over `keydown`/`keyup`, so nothing here favours one over the
 * other.
 *
 * ## Why amber, not red
 *
 * `Button.tsx`'s `danger` variant is explicitly reserved and explicitly not
 * for this: raising an emergency is a calm, deliberate request for help, not
 * something to colour as risky or alarming — the same reasoning that keeps
 * the wordmark a quiet shield rather than a siren (`AppShell.tsx`). Amber
 * already carries the "emergency" meaning in this app (the incident queue's
 * own badge), so this reuses it rather than inventing a second vocabulary.
 */

export const SOS_HOLD_MS = 1500

type Phase = 'idle' | 'holding' | 'submitting' | 'error'

export function SosButton() {
  const { account, api } = useAuth()
  const navigate = useNavigate()

  const [phase, setPhase] = useState<Phase>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const positionRef = useRef<ReturnType<typeof getEmergencyPosition> | null>(null)
  const firedRef = useRef(false)

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => clearTimer, [clearTimer])

  const submit = useCallback(async () => {
    setPhase('submitting')
    setErrorMessage(null)
    try {
      // Whatever the geolocation read has by now — resolved, still pending
      // and about to be abandoned, or never started — this never waits
      // beyond what the hold itself already gave it. Missing coordinates
      // are a normal, fully-supported outcome, not an error.
      const position = (await positionRef.current) ?? null
      const result = await api.triggerEmergency(
        position ? { latitude: position.latitude, longitude: position.longitude } : {},
      )
      setPhase('idle')
      navigate('/emergency/submitted', { replace: false, state: { result } })
    } catch (cause) {
      setPhase('error')
      setErrorMessage(
        cause instanceof ApiError
          ? cause.message
          : 'Could not send the alert. Check your connection and try again.',
      )
    }
  }, [api, navigate])

  const begin = useCallback(() => {
    if (phase === 'submitting') return
    firedRef.current = false
    setErrorMessage(null)
    setPhase('holding')
    // Started now, in parallel with the hold, so a fix that arrives quickly
    // costs nothing — by the time the hold completes, it's often already
    // there.
    positionRef.current = getEmergencyPosition()
    clearTimer()
    timerRef.current = setTimeout(() => {
      firedRef.current = true
      void submit()
    }, SOS_HOLD_MS)
  }, [phase, clearTimer, submit])

  const cancel = useCallback(() => {
    clearTimer()
    if (!firedRef.current) {
      setPhase((current) => (current === 'holding' ? 'idle' : current))
    }
  }, [clearTimer])

  // SOS is a student-facing emergency trigger. Staff users (security, ICC, admin)
  // are responders and investigators, not report submitters.
  if (!account || account.role !== 'student') return null

  const holding = phase === 'holding'
  const submitting = phase === 'submitting'

  return (
    <div className="fixed right-4 bottom-4 z-50 flex flex-col items-end gap-2 sm:right-6 sm:bottom-6">
      {phase === 'error' && errorMessage && (
        <div
          role="alert"
          className="max-w-64 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900 shadow-sm"
        >
          {errorMessage}
        </div>
      )}

      <button
        type="button"
        disabled={submitting}
        aria-label={
          submitting ? 'Sending emergency alert' : 'Emergency — press and hold to get help now'
        }
        aria-live="polite"
        onPointerDown={(event) => {
          event.preventDefault()
          begin()
        }}
        onPointerUp={cancel}
        onPointerLeave={cancel}
        onPointerCancel={cancel}
        onKeyDown={(event) => {
          if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) {
            event.preventDefault()
            begin()
          }
        }}
        onKeyUp={(event) => {
          if (event.key === 'Enter' || event.key === ' ') cancel()
        }}
        className={[
          'relative isolate flex min-h-16 min-w-16 select-none items-center justify-center overflow-hidden rounded-full text-center text-xs font-semibold shadow-lg outline-none transition-colors',
          'focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2',
          submitting
            ? 'bg-amber-300 text-amber-900'
            : 'bg-amber-100 text-amber-900 hover:bg-amber-200 active:bg-amber-300',
        ].join(' ')}
      >
        {/* The hold's progress, filling bottom-to-top behind the label. A
            plain CSS transition rather than per-frame JS: the duration is
            fixed, so the browser can animate it without our help, and a
            cancelled hold just resets the transform instead of needing to
            unwind a running loop. */}
        <span
          aria-hidden="true"
          className="absolute inset-0 origin-bottom bg-amber-400"
          style={{
            transform: `scaleY(${holding ? 1 : 0})`,
            transition: holding
              ? `transform ${SOS_HOLD_MS}ms linear`
              : 'transform 150ms ease-out',
          }}
        />
        <span className="relative leading-tight">
          {submitting ? 'Sending…' : holding ? 'Keep holding…' : 'SOS'}
        </span>
      </button>
    </div>
  )
}
