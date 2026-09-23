export interface SavedAnonymousReport {
  public_ref: string
  access_token: string
  submitted_at: string
}

const STORAGE_KEY = 'campusshield-anonymous-reports'

export function loadSavedAnonymousReports(): SavedAnonymousReport[] {
  if (typeof window === 'undefined') return []

  try {
    const storage = window.localStorage
    if (!storage || typeof storage.getItem !== 'function') return []
    const value: unknown = JSON.parse(storage.getItem(STORAGE_KEY) ?? '[]')
    if (!Array.isArray(value)) return []
    return value.filter(
      (item): item is SavedAnonymousReport =>
        typeof item === 'object' &&
        item !== null &&
        typeof item.public_ref === 'string' &&
        typeof item.access_token === 'string' &&
        typeof item.submitted_at === 'string',
    )
  } catch {
    return []
  }
}

export function saveAnonymousReport(report: SavedAnonymousReport): void {
  if (typeof window === 'undefined') return

  try {
    const storage = window.localStorage
    if (!storage || typeof storage.setItem !== 'function') return
    const existing = loadSavedAnonymousReports().filter(
      (item) => item.public_ref !== report.public_ref,
    )
    storage.setItem(STORAGE_KEY, JSON.stringify([report, ...existing]))
  } catch {
    // Private browsing and test environments may disable local storage.
  }
}