import type { ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  className?: string
  as?: 'div' | 'section' | 'article'
}

export function Card({ children, className = '', as: Tag = 'div' }: CardProps) {
  return (
    <Tag
      className={[
        'border-ink-200 rounded-xl border bg-white shadow-sm',
        className || 'p-5 sm:p-6',
      ].join(' ')}
    >
      {children}
    </Tag>
  )
}

interface StatCardProps {
  label: string
  value: ReactNode
  /** Context shown under the value — often the reason a number is what it is. */
  detail?: ReactNode
  loading?: boolean
}

export function StatCard({ label, value, detail, loading = false }: StatCardProps) {
  return (
    <Card className="p-4 sm:p-5">
      <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">{label}</dt>
      <dd className="mt-2">
        {loading ? (
          <div
            className="bg-ink-200 h-8 w-16 animate-pulse rounded"
            aria-hidden="true"
            data-testid="stat-skeleton"
          />
        ) : (
          <span className="text-ink-900 text-3xl font-semibold tabular-nums">{value}</span>
        )}
        {detail && <p className="text-ink-500 mt-1.5 text-xs leading-relaxed">{detail}</p>}
      </dd>
    </Card>
  )
}
