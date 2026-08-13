import { useId, type ReactNode, type SelectHTMLAttributes } from 'react'

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: string
  hint?: ReactNode
  error?: string
  id?: string
  placeholder?: string
  children: ReactNode
}

/**
 * A native `<select>`.
 *
 * Deliberately not a custom dropdown. The native control gets the platform
 * picker on a phone — a full-height, thumb-reachable wheel that works with
 * VoiceOver and TalkBack — and no hand-built listbox matches that.
 */
export function Select({
  label,
  hint,
  error,
  id,
  placeholder,
  children,
  className = '',
  ...props
}: SelectProps) {
  const generatedId = useId()
  const fieldId = id ?? generatedId
  const hintId = `${fieldId}-hint`
  const errorId = `${fieldId}-error`
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ')

  return (
    <div className="space-y-1.5">
      <label htmlFor={fieldId} className="text-ink-700 block text-sm font-medium">
        {label}
      </label>

      {hint && (
        <p id={hintId} className="text-ink-500 text-xs leading-relaxed">
          {hint}
        </p>
      )}

      <select
        {...props}
        id={fieldId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={[
          'w-full rounded-lg border bg-white px-3 py-2.5 text-base',
          'transition-colors duration-150',
          error
            ? 'border-red-400 focus:border-red-500'
            : 'border-ink-300 focus:border-brand-500',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {placeholder && <option value="">{placeholder}</option>}
        {children}
      </select>

      {error && (
        <p id={errorId} role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}
