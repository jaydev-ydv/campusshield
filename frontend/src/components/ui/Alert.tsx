import type { ReactNode } from 'react'

type Tone = 'info' | 'success' | 'warning' | 'error'

interface AlertProps {
  tone?: Tone
  title?: string
  children: ReactNode
  /** Shown small and monospaced — quotable when reporting a problem. */
  requestId?: string
  className?: string
}

const TONES: Record<Tone, { box: string; icon: string; glyph: string }> = {
  info: {
    box: 'bg-brand-50 border-brand-200 text-brand-900',
    icon: 'text-brand-700',
    glyph: 'i',
  },
  success: {
    box: 'bg-emerald-50 border-emerald-200 text-emerald-900',
    icon: 'text-emerald-700',
    glyph: '✓',
  },
  warning: {
    box: 'bg-amber-50 border-amber-200 text-amber-900',
    icon: 'text-amber-700',
    glyph: '!',
  },
  error: { box: 'bg-red-50 border-red-200 text-red-900', icon: 'text-red-700', glyph: '!' },
}

export function Alert({
  tone = 'info',
  title,
  children,
  requestId,
  className = '',
}: AlertProps) {
  const style = TONES[tone]
  return (
    <div
      // Errors interrupt; everything else is announced politely when the user
      // next pauses. An alert role on a success message talks over whatever a
      // screen reader user was already listening to.
      role={tone === 'error' ? 'alert' : 'status'}
      aria-live={tone === 'error' ? 'assertive' : 'polite'}
      className={['rounded-lg border p-4', style.box, className].filter(Boolean).join(' ')}
    >
      <div className="flex gap-3">
        <span
          aria-hidden="true"
          className={[
            'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full',
            'border border-current text-xs font-bold',
            style.icon,
          ].join(' ')}
        >
          {style.glyph}
        </span>
        <div className="min-w-0 flex-1 text-sm">
          {title && <p className="mb-1 font-semibold">{title}</p>}
          <div className="leading-relaxed">{children}</div>
          {requestId && requestId !== 'client' && (
            <p className="mt-2 font-mono text-xs opacity-70">Reference: {requestId}</p>
          )}
        </div>
      </div>
    </div>
  )
}
