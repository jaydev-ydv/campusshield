import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'

import { useAuth } from '../../auth/useAuth'
import { useUnreadCount } from '../../hooks/useNotifications'
import { ROLE_LABELS } from '../../lib/api'
import { SosButton } from '../SosButton'
import { Button } from '../ui/Button'

function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      {/* A shield outline, drawn quietly. No siren, no exclamation mark, no
          warning triangle: the iconography of alarm makes an app harder to open
          at exactly the moment someone is deciding whether to bother. */}
      <svg
        viewBox="0 0 24 24"
        className="text-brand-700 h-6 w-6 shrink-0"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M12 3l7 3v5.5c0 4.2-2.9 7.9-7 9-4.1-1.1-7-4.8-7-9V6l7-3z" />
        <path d="M9.5 12.2l1.9 1.9 3.4-3.6" />
      </svg>
      <span className="text-ink-900 text-lg font-semibold tracking-tight">
        Campus<span className="text-brand-700">Shield</span>
      </span>
      {!compact && (
        <span className="sr-only">— report safely, help improve campus safety</span>
      )}
    </span>
  )
}

const NAV_LINKS = [
  { to: '/dashboard', label: 'Overview', end: true },
  { to: '/report', label: 'Report', end: false },
  { to: '/reports', label: 'My reports', end: true },
  { to: '/account', label: 'Account', end: true },
] as const

/** The responder link. Shown to staff, hidden from students.
 *
 *  Tidiness, not access control — every endpoint behind it refuses a student
 *  server-side, and the page itself explains why when a student reaches it
 *  directly. */
const RESPONDER_LINK = { to: '/incidents', label: 'Incidents', end: true } as const

function navLinksFor(role: string | undefined) {
  return role && role !== 'student' ? [...NAV_LINKS, RESPONDER_LINK] : NAV_LINKS
}

function RoleBadge({ role }: { role: keyof typeof ROLE_LABELS }) {
  return (
    <span className="bg-brand-50 text-brand-800 ring-brand-200 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset">
      {ROLE_LABELS[role]}
    </span>
  )
}

/**
 * A link to the notification inbox with an unread badge.
 *
 * The count is polled, not pushed — see `useUnreadCount`. Overstating what
 * "unread" means here would be worse than a plain badge, so the icon carries
 * no animation or urgency styling of its own; it is the same visual weight
 * whether the count is 1 or 40.
 */
function NotificationBell() {
  const { unreadCount } = useUnreadCount()
  const hasUnread = unreadCount > 0

  return (
    <Link
      to="/notifications"
      className="border-ink-300 text-ink-700 hover:bg-ink-50 relative inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border"
      aria-label={
        hasUnread ? `Notifications, ${unreadCount} unread` : 'Notifications, none unread'
      }
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
      {hasUnread && (
        <span
          aria-hidden="true"
          className="absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white"
        >
          {unreadCount > 9 ? '9+' : unreadCount}
        </span>
      )}
    </Link>
  )
}

export function Navigation() {
  const { account, logout } = useAuth()
  const [busy, setBusy] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const links = navLinksFor(account?.role)

  async function handleLogout() {
    setBusy(true)
    try {
      await logout()
    } finally {
      setBusy(false)
      setMenuOpen(false)
    }
  }

  return (
    <header className="border-ink-200 sticky top-0 z-40 border-b bg-white/95 backdrop-blur">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3 sm:px-6"
      >
        <Link to="/dashboard" className="rounded-md">
          <Wordmark />
        </Link>

        {/* Desktop */}
        <div className="hidden items-center gap-4 sm:flex">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                [
                  'rounded-md px-2 py-1 text-sm font-medium transition-colors',
                  isActive ? 'text-brand-800' : 'text-ink-600 hover:text-ink-900',
                ].join(' ')
              }
            >
              {link.label}
            </NavLink>
          ))}
          {account && (
            <div className="flex items-center gap-3">
              <NotificationBell />
              <div className="text-right">
                <p className="text-ink-800 max-w-[16rem] truncate text-sm font-medium">
                  {account.email}
                </p>
                <RoleBadge role={account.role} />
              </div>
              <Button variant="secondary" size="sm" onClick={handleLogout} loading={busy}>
                Sign out
              </Button>
            </div>
          )}
        </div>

        {/* Mobile */}
        {account && (
          <div className="flex items-center gap-2 sm:hidden">
            <NotificationBell />
            <button
              type="button"
              className="border-ink-300 text-ink-700 rounded-lg border p-2"
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              onClick={() => setMenuOpen((open) => !open)}
            >
              <svg
                viewBox="0 0 24 24"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                aria-hidden="true"
              >
                {menuOpen ? (
                  <path d="M6 6l12 12M18 6L6 18" />
                ) : (
                  <path d="M4 7h16M4 12h16M4 17h16" />
                )}
              </svg>
            </button>
          </div>
        )}
      </nav>

      {menuOpen && account && (
        <div id="mobile-menu" className="border-ink-200 border-t bg-white px-4 py-3 sm:hidden">
          <nav aria-label="Sections" className="mb-4 flex flex-col gap-1">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                onClick={() => setMenuOpen(false)}
                className={({ isActive }) =>
                  [
                    'rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
                    isActive ? 'bg-brand-50 text-brand-800' : 'text-ink-700 hover:bg-ink-50',
                  ].join(' ')
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
          <p className="text-ink-800 truncate text-sm font-medium">{account.email}</p>
          <div className="mt-1.5">
            <RoleBadge role={account.role} />
          </div>
          <Button
            variant="secondary"
            size="md"
            fullWidth
            className="mt-3"
            onClick={handleLogout}
            loading={busy}
          >
            Sign out
          </Button>
        </div>
      )}
    </header>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <Navigation />
      <main id="main" className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        {children}
      </main>
      <SiteFooter />
      <SosButton />
    </div>
  )
}

export function SiteFooter() {
  return (
    <footer className="border-ink-200 mt-auto border-t bg-white">
      <div className="text-ink-500 mx-auto max-w-5xl px-4 py-6 text-xs leading-relaxed sm:px-6">
        <p className="text-ink-600 font-medium">Report safely. Help improve campus safety.</p>
        <p className="mt-2 max-w-2xl">
          CampusShield supports your institution&rsquo;s existing safety and grievance
          mechanisms. It does not replace the Internal Complaints Committee, and it does not
          determine what happened or who was responsible.
        </p>
        <p className="mt-2">
          In immediate danger, contact campus security or emergency services directly.
        </p>
      </div>
    </footer>
  )
}

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <div className="flex flex-1 items-center justify-center px-4 py-8 sm:px-6 sm:py-12">
        <main id="main" className="w-full max-w-md">
          <div className="mb-7 text-center">
            <Wordmark />
            <h1 className="text-ink-900 mt-5 text-2xl font-semibold tracking-tight">
              {title}
            </h1>
            {subtitle && (
              <div className="text-ink-600 mt-2 text-sm leading-relaxed">{subtitle}</div>
            )}
          </div>
          <div className="border-ink-200 rounded-xl border bg-white p-5 shadow-sm sm:p-7">
            {children}
          </div>
          {footer && <div className="text-ink-600 mt-5 text-center text-sm">{footer}</div>}
        </main>
      </div>
      <SiteFooter />
    </div>
  )
}
