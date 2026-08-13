import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { Spinner } from './Spinner'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  loadingLabel?: string
  fullWidth?: boolean
  children: ReactNode
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand-700 text-white hover:bg-brand-800 active:bg-brand-900 shadow-sm',
  secondary: 'bg-white text-ink-800 border border-ink-300 hover:bg-ink-50 active:bg-ink-100',
  ghost: 'bg-transparent text-brand-800 hover:bg-brand-50 active:bg-brand-100',
  // Reserved for destructive confirmation. Not used for emergency reporting:
  // raising an emergency is a calm, clearly-labelled choice, not a red panic
  // button, and colouring it as danger would suggest the user is doing
  // something risky by asking for help.
  danger: 'bg-red-700 text-white hover:bg-red-800 active:bg-red-900 shadow-sm',
}

const SIZES: Record<Size, string> = {
  // Minimum 44px tall at md and lg — the smallest comfortable touch target, and
  // this is a phone-first product used one-handed and often in a hurry.
  sm: 'text-sm px-3 py-1.5 min-h-9',
  md: 'text-sm px-4 py-2.5 min-h-11',
  lg: 'text-base px-5 py-3 min-h-12',
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  loadingLabel = 'Working…',
  fullWidth = false,
  disabled,
  className = '',
  children,
  ...props
}: ButtonProps) {
  const isDisabled = disabled || loading
  return (
    <button
      {...props}
      disabled={isDisabled}
      // Announced to assistive technology, which otherwise gets no signal that
      // anything is happening between click and response.
      aria-busy={loading || undefined}
      className={[
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium',
        'transition-colors duration-150',
        'disabled:cursor-not-allowed disabled:opacity-60',
        VARIANTS[variant],
        SIZES[size],
        fullWidth ? 'w-full' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {loading && <Spinner size="sm" />}
      <span>{loading ? loadingLabel : children}</span>
    </button>
  )
}
