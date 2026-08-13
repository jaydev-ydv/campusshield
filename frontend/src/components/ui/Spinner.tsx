interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg'
  /** Visible caption. Omit inside a button, which announces its own busy state. */
  label?: string
  className?: string
}

const SIZES = { sm: 'h-4 w-4 border-2', md: 'h-6 w-6 border-2', lg: 'h-9 w-9 border-[3px]' }

export function Spinner({ size = 'md', label, className = '' }: SpinnerProps) {
  return (
    <span className={['inline-flex items-center gap-2', className].filter(Boolean).join(' ')}>
      <span
        // Decorative: the surrounding element carries the accessible status, so
        // announcing the spinner itself would say the same thing twice.
        aria-hidden="true"
        className={[
          'animate-spin rounded-full border-current border-t-transparent opacity-70',
          SIZES[size],
        ].join(' ')}
      />
      {label && <span className="text-ink-600 text-sm">{label}</span>}
    </span>
  )
}

interface LoadingStateProps {
  label?: string
  className?: string
}

/** Full-block loading state, for a region whose content is not ready. */
export function LoadingState({ label = 'Loading…', className = '' }: LoadingStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={['flex items-center justify-center py-12', className]
        .filter(Boolean)
        .join(' ')}
    >
      <Spinner size="lg" />
      <span className="sr-only">{label}</span>
    </div>
  )
}
