import { useCallback, useState } from 'react'

export type AudioAlertStatus =
  'uninitialized' | 'enabled' | 'muted' | 'blocked' | 'unsupported'

export interface AudioContextFactory {
  (): AudioContext
}

const STORAGE_MUTE_KEY = 'campusshield_audio_muted'
const STORAGE_ENABLED_KEY = 'campusshield_audio_enabled'

/**
 * Service to manage Web Audio API state and synthesize emergency SOS alert beeps.
 *
 * ## Key features:
 * - Uses Web Audio API zero-dependency tone synthesizer (dual-pulse alert).
 * - Respects modern browser autoplay restrictions (unlocked via explicit user gesture).
 * - Immediately plays an audible confirmation chirp upon enabling so users know it works.
 * - Auto-resumes AudioContext if suspended by browser power-saving.
 * - Handles closed AudioContext re-creation on hardware or device switch.
 * - Avoids overlapping / audio distortion: queues or sequences simultaneous emergency alerts.
 * - Supports muting / unmuting with persistence in localStorage.
 * - Fails safely if AudioContext is unsupported or blocked.
 */
export class EmergencyAudioService {
  private ctx: AudioContext | null = null
  private status: AudioAlertStatus = 'uninitialized'
  private isMuted: boolean = false
  private isPlaying: boolean = false
  private queued: boolean = false
  private customFactory?: AudioContextFactory

  constructor(customFactory?: AudioContextFactory) {
    this.customFactory = customFactory

    // Read stored preferences safely
    try {
      if (
        typeof window !== 'undefined' &&
        typeof window.localStorage?.getItem === 'function'
      ) {
        const storedMuted = window.localStorage.getItem(STORAGE_MUTE_KEY)
        if (storedMuted !== null) {
          this.isMuted = storedMuted === 'true'
        }

        const storedEnabled = window.localStorage.getItem(STORAGE_ENABLED_KEY)
        if (storedEnabled === 'true') {
          this.status = this.isMuted ? 'muted' : 'enabled'
        }
      }
    } catch {
      // Ignore localStorage access failures
    }

    if (!this.customFactory) {
      this.setupAutoUnlock()
    }
  }

  private setupAutoUnlock(): void {
    if (typeof window === 'undefined') return
    const unlock = () => {
      if (this.status === 'uninitialized' || this.status === 'blocked') {
        void this.enable()
      }
      window.removeEventListener('click', unlock, true)
      window.removeEventListener('keydown', unlock, true)
      window.removeEventListener('pointerdown', unlock, true)
    }
    window.addEventListener('click', unlock, true)
    window.addEventListener('keydown', unlock, true)
    window.addEventListener('pointerdown', unlock, true)
  }

  isSupported(): boolean {
    if (this.customFactory) return true
    return (
      typeof window !== 'undefined' &&
      (typeof window.AudioContext === 'function' ||
        typeof (window as unknown as { webkitAudioContext?: unknown }).webkitAudioContext ===
          'function')
    )
  }

  getStatus(): AudioAlertStatus {
    if (!this.isSupported()) return 'unsupported'
    return this.status
  }

  getMuted(): boolean {
    return this.isMuted
  }

  setMuted(muted: boolean): void {
    this.isMuted = muted
    try {
      if (
        typeof window !== 'undefined' &&
        typeof window.localStorage?.setItem === 'function'
      ) {
        window.localStorage.setItem(STORAGE_MUTE_KEY, String(muted))
      }
    } catch {
      // Ignore storage errors
    }
    if (this.status === 'enabled' || this.status === 'muted') {
      this.status = muted ? 'muted' : 'enabled'
    }
  }

  toggleMute(): boolean {
    this.setMuted(!this.isMuted)
    return this.isMuted
  }

  private getOrCreateContext(): AudioContext | null {
    if (this.customFactory) {
      if (!this.ctx || this.ctx.state === 'closed') {
        this.ctx = this.customFactory()
      }
      return this.ctx
    }

    if (!this.isSupported()) return null

    if (!this.ctx || this.ctx.state === 'closed') {
      try {
        const AudioContextClass =
          typeof window !== 'undefined'
            ? window.AudioContext ||
              (window as unknown as { webkitAudioContext: typeof AudioContext })
                .webkitAudioContext
            : undefined
        if (typeof AudioContextClass === 'function') {
          this.ctx = new AudioContextClass()
        } else {
          return null
        }
      } catch {
        return null
      }
    }
    return this.ctx
  }

  async enable(): Promise<boolean> {
    if (!this.isSupported()) {
      this.status = 'unsupported'
      return false
    }

    try {
      const ctx = this.getOrCreateContext()
      if (!ctx) {
        this.status = 'unsupported'
        return false
      }

      if (ctx.state === 'suspended') {
        await ctx.resume()
      }

      if (ctx.state !== 'running') {
        this.status = 'blocked'
        return false
      }

      // Explicitly unmute when user chooses to enable sound alerts
      this.setMuted(false)
      this.status = 'enabled'

      try {
        if (
          typeof window !== 'undefined' &&
          typeof window.localStorage?.setItem === 'function'
        ) {
          window.localStorage.setItem(STORAGE_ENABLED_KEY, 'true')
        }
      } catch {
        // Ignore storage errors
      }

      return true
    } catch {
      this.status = 'blocked'
      return false
    }
  }

  async playConfirmationChirp(): Promise<void> {
    const ctx = this.getOrCreateContext()
    if (!ctx || ctx.state === 'closed') return

    try {
      if (ctx.state === 'suspended') {
        await ctx.resume()
      }

      const now = ctx.currentTime
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()

      osc.type = 'sine'
      osc.frequency.setValueAtTime(880, now)

      gain.gain.setValueAtTime(0.001, now)
      gain.gain.exponentialRampToValueAtTime(0.2, now + 0.02)
      gain.gain.setValueAtTime(0.2, now + 0.1)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12)

      osc.connect(gain)
      gain.connect(ctx.destination)

      osc.start(now)
      osc.stop(now + 0.12)
    } catch {
      // Ignore chirp failure
    }
  }

  async playEmergencyBeep(): Promise<void> {
    if (this.isMuted || this.status === 'muted') {
      return
    }

    if (this.status === 'uninitialized') {
      await this.enable()
    }

    if (this.status !== 'enabled') {
      return
    }

    const ctx = this.getOrCreateContext()
    if (!ctx || ctx.state === 'closed') {
      return
    }

    // Auto-resume if browser power-saver or background tab suspended it
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume()
      } catch {
        this.status = 'blocked'
        return
      }
    }

    // Prevent overlapping sounds: queue a single follow-up tone
    if (this.isPlaying) {
      this.queued = true
      return
    }

    this.isPlaying = true
    try {
      await this.synthesizeDualPulseTone()
    } finally {
      this.isPlaying = false
      if (this.queued) {
        this.queued = false
        // Play queued tone with small spacing to prevent audio overlap
        setTimeout(() => {
          void this.playEmergencyBeep()
        }, 150)
      }
    }
  }

  private synthesizeDualPulseTone(): Promise<void> {
    return new Promise((resolve) => {
      const ctx = this.getOrCreateContext()
      if (!ctx || ctx.state === 'closed') {
        resolve()
        return
      }

      try {
        const now = ctx.currentTime

        // Pulse 1: 880Hz (A5) for 120ms
        const osc1 = ctx.createOscillator()
        const gain1 = ctx.createGain()
        osc1.type = 'sine'
        osc1.frequency.setValueAtTime(880, now)

        gain1.gain.setValueAtTime(0.001, now)
        gain1.gain.exponentialRampToValueAtTime(0.2, now + 0.02)
        gain1.gain.setValueAtTime(0.2, now + 0.1)
        gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.12)

        osc1.connect(gain1)
        gain1.connect(ctx.destination)
        osc1.start(now)
        osc1.stop(now + 0.12)

        // Pulse 2: 1046.5Hz (C6) starting at now + 0.16s for 140ms
        const osc2 = ctx.createOscillator()
        const gain2 = ctx.createGain()
        osc2.type = 'sine'
        osc2.frequency.setValueAtTime(1046.5, now + 0.16)

        gain2.gain.setValueAtTime(0.001, now + 0.16)
        gain2.gain.exponentialRampToValueAtTime(0.25, now + 0.18)
        gain2.gain.setValueAtTime(0.25, now + 0.28)
        gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.3)

        osc2.connect(gain2)
        gain2.connect(ctx.destination)
        osc2.start(now + 0.16)
        osc2.stop(now + 0.3)

        osc2.onended = () => {
          resolve()
        }

        // Safety fallback timer if onended is not called in test mocks
        setTimeout(resolve, 350)
      } catch {
        resolve()
      }
    })
  }

  close(): void {
    if (this.ctx && this.ctx.state !== 'closed') {
      try {
        void this.ctx.close()
      } catch {
        // Ignore close error
      }
    }
    this.ctx = null
  }
}

let sharedAudioService: EmergencyAudioService | null = null

export function getSharedEmergencyAudioService(): EmergencyAudioService {
  if (!sharedAudioService) {
    sharedAudioService = new EmergencyAudioService()
  }
  return sharedAudioService
}

export function resetSharedAudioServiceForTesting(): void {
  if (sharedAudioService) {
    sharedAudioService.close()
    sharedAudioService = null
  }
}

/**
 * React hook to manage audio alerts inside responder dashboard components.
 */
export function useEmergencyAudioAlert(serviceInstance?: EmergencyAudioService) {
  const [service] = useState(() => serviceInstance ?? getSharedEmergencyAudioService())
  const [audioStatus, setAudioStatus] = useState<AudioAlertStatus>(() => service.getStatus())
  const [isMuted, setIsMuted] = useState<boolean>(() => service.getMuted())

  const enableAudio = useCallback(async () => {
    const ok = await service.enable()
    if (ok) {
      await service.playConfirmationChirp()
    }
    setAudioStatus(service.getStatus())
    setIsMuted(service.getMuted())
    return ok
  }, [service])

  const playTestSound = useCallback(async () => {
    await service.playConfirmationChirp()
  }, [service])

  const toggleMute = useCallback(() => {
    const muted = service.toggleMute()
    setIsMuted(muted)
    setAudioStatus(service.getStatus())
  }, [service])

  const playEmergencyAlert = useCallback(async () => {
    await service.playEmergencyBeep()
  }, [service])

  return {
    audioStatus,
    isMuted,
    enableAudio,
    playTestSound,
    toggleMute,
    playEmergencyAlert,
    service,
  }
}
