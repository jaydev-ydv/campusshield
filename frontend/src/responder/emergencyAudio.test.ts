import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EmergencyAudioService } from './emergencyAudio'

const store = new Map<string, string>()
const mockLocalStorage = {
  getItem: (key: string) => store.get(key) ?? null,
  setItem: (key: string, val: string) => store.set(key, val),
  removeItem: (key: string) => store.delete(key),
  clear: () => store.clear(),
}

Object.defineProperty(window, 'localStorage', {
  configurable: true,
  value: mockLocalStorage,
})

function createMockAudioContext(initialState: AudioContextState = 'suspended') {
  let state = initialState
  const createdNodes: {
    oscillators: Array<{
      type: string
      frequency: { value: number; setValueAtTime: ReturnType<typeof vi.fn> }
      start: ReturnType<typeof vi.fn>
      stop: ReturnType<typeof vi.fn>
      connect: ReturnType<typeof vi.fn>
      onended: (() => void) | null
    }>
    gains: Array<{
      gain: {
        value: number
        setValueAtTime: ReturnType<typeof vi.fn>
        exponentialRampToValueAtTime: ReturnType<typeof vi.fn>
      }
      connect: ReturnType<typeof vi.fn>
    }>
  } = { oscillators: [], gains: [] }

  const resume = vi.fn(async () => {
    state = 'running'
  })

  const close = vi.fn(async () => {
    state = 'closed'
  })

  const mockContext = {
    get state() {
      return state
    },
    currentTime: 10,
    destination: {},
    resume,
    close,
    createOscillator: vi.fn(() => {
      const osc = {
        type: 'sine',
        frequency: {
          value: 440,
          setValueAtTime: vi.fn(),
        },
        start: vi.fn(),
        stop: vi.fn((_when?: number) => {
          setTimeout(() => {
            if (osc.onended) osc.onended()
          }, 10)
        }),
        connect: vi.fn(),
        onended: null as (() => void) | null,
      }
      createdNodes.oscillators.push(osc)
      return osc
    }),
    createGain: vi.fn(() => {
      const g = {
        gain: {
          value: 1,
          setValueAtTime: vi.fn(),
          exponentialRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
      }
      createdNodes.gains.push(g)
      return g
    }),
    createdNodes,
  }

  return mockContext
}

describe('EmergencyAudioService', () => {
  beforeEach(() => {
    store.clear()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('reports unsupported when AudioContext is missing in global window', () => {
    const service = new EmergencyAudioService()
    expect(service.isSupported()).toBe(false)
    expect(service.getStatus()).toBe('unsupported')
  })

  it('initializes and unlocks audio on enable() with custom factory', async () => {
    const mockCtx = createMockAudioContext('suspended')
    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)

    expect(service.getStatus()).toBe('uninitialized')
    const ok = await service.enable()

    expect(ok).toBe(true)
    expect(service.getStatus()).toBe('enabled')
    expect(mockCtx.resume).toHaveBeenCalled()
  })

  it('transitions to blocked status if resume fails or remains suspended', async () => {
    const mockCtx = createMockAudioContext('suspended')
    mockCtx.resume = vi.fn(async () => {
      // Browser policy blocked playback; state stays suspended
    })

    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)
    const ok = await service.enable()

    expect(ok).toBe(false)
    expect(service.getStatus()).toBe('blocked')
  })

  it('toggles mute state and persists in localStorage', () => {
    const mockCtx = createMockAudioContext('running')
    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)

    expect(service.getMuted()).toBe(false)
    service.toggleMute()
    expect(service.getMuted()).toBe(true)
    expect(store.get('campusshield_audio_muted')).toBe('true')

    service.toggleMute()
    expect(service.getMuted()).toBe(false)
    expect(store.get('campusshield_audio_muted')).toBe('false')
  })

  it('synthesizes dual-pulse emergency beep when enabled and playing', async () => {
    const mockCtx = createMockAudioContext('running')
    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)
    await service.enable()

    await service.playEmergencyBeep()

    // Dual-pulse: should have created 2 oscillators and 2 gain nodes
    expect(mockCtx.createdNodes.oscillators.length).toBe(2)
    expect(mockCtx.createdNodes.gains.length).toBe(2)

    // Pulse 1: 880Hz
    expect(mockCtx.createdNodes.oscillators[0].frequency.setValueAtTime).toHaveBeenCalledWith(
      880,
      expect.any(Number),
    )
    // Pulse 2: 1046.5Hz
    expect(mockCtx.createdNodes.oscillators[1].frequency.setValueAtTime).toHaveBeenCalledWith(
      1046.5,
      expect.any(Number),
    )
  })

  it('does not synthesize beep when muted', async () => {
    const mockCtx = createMockAudioContext('running')
    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)
    await service.enable()
    service.setMuted(true)

    await service.playEmergencyBeep()

    expect(mockCtx.createdNodes.oscillators.length).toBe(0)
  })

  it('prevents overlapping sounds by queueing sequential playback', async () => {
    const mockCtx = createMockAudioContext('running')
    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)
    await service.enable()

    // Trigger multiple beeps concurrently
    const p1 = service.playEmergencyBeep()
    const p2 = service.playEmergencyBeep()

    await Promise.all([p1, p2])

    // First dual-pulse (2 oscillators) created immediately
    expect(mockCtx.createdNodes.oscillators.length).toBeGreaterThanOrEqual(2)
  })

  it('synthesizes confirmation chirp on playConfirmationChirp()', async () => {
    const mockCtx = createMockAudioContext('running')
    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)
    await service.enable()
    await service.playConfirmationChirp()

    expect(mockCtx.createdNodes.oscillators.length).toBe(1)
    expect(mockCtx.createdNodes.oscillators[0].frequency.setValueAtTime).toHaveBeenCalledWith(
      880,
      expect.any(Number),
    )
  })

  it('closes audio context safely', async () => {
    const mockCtx = createMockAudioContext('running')
    const service = new EmergencyAudioService(() => mockCtx as unknown as AudioContext)
    await service.enable()
    service.close()

    expect(mockCtx.close).toHaveBeenCalled()
  })
})
