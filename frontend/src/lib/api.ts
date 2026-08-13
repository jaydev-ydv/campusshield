/** Typed views of the endpoints Phase 3A consumes. */
import type { ApiClient } from './apiClient'

export type UserRole = 'student' | 'security' | 'icc' | 'admin'
export type ReportKind = 'incident' | 'concern'
export type ReporterRelationship = 'affected' | 'witness' | 'third_party'
export type SubmissionMode = 'identified' | 'anonymous'

export interface AccountIdentity {
  user_id: string
  email: string
  role: UserRole
  is_active: boolean
  /**
   * Always `null` for a student — `identity.app_user` has a CHECK constraint
   * forbidding a student row from carrying one at all, so there is nothing
   * here to leak. Present and editable only for security/ICC/admin accounts.
   */
  display_name: string | null
  created?: boolean
}

export type NotificationCategory = 'status_update' | 'assignment' | 'alert' | 'system'

/**
 * `serialize_notification`. Deliberately excludes `delivery_state` and
 * `fcm_message_id` — this project sends no push, and a client-visible
 * "delivered" flag would imply one.
 */
export interface AppNotification {
  notification_id: string
  category: NotificationCategory
  title: string
  body: string
  related_public_ref: string | null
  created_at: string
  read_at: string | null
}

export interface NotificationsResponse extends Paginated<AppNotification> {
  unread_count: number
}

export const NOTIFICATION_CATEGORY_LABELS: Record<NotificationCategory, string> = {
  status_update: 'Status update',
  assignment: 'Case assigned',
  alert: 'Alert',
  system: 'System',
}

export interface CampusLocation {
  location_id: number
  code: string
  name: string
  location_type: string | null
  zone: { zone_id: number; code: string; name: string } | null
  latitude: number | null
  longitude: number | null
  is_indoor: boolean | null
  dispatch_note: string | null
  /** True only for a demo/development fixture — never a real surveyed location. */
  is_synthetic: boolean
}

export interface ReportCategory {
  category_id: number
  code: string
  label: string
  kind: ReportKind
  emergency_eligible: boolean
  requires_confidentiality: boolean
}

/** Matches `serialize_report_summary`. Carries no reporter identity. */
export interface ReportSummary {
  public_ref: string
  report_kind: ReportKind
  submission_mode: SubmissionMode
  reporter_relationship: ReporterRelationship
  category: { category_id: number; code: string; label: string } | null
  location: { location_id: number; code: string; name: string }
  location_hint: string | null
  occurred_at: string
  submitted_at: string
  is_emergency: boolean
  is_ongoing: boolean
  reporter_contactable: boolean
  status: CaseStatus
}

/** `serialize_report_detail`. The reporter's own view of their case history —
 *  filtered server-side to `visible_to_reporter` rows, a strict subset of
 *  what `IncidentDetail.case_status_history` (the responder view) carries. */
export interface ReportDetail extends ReportSummary {
  narrative: string | null
  narrative_available: boolean
  narrative_withheld_reason?: string
  evidence_count: number
  status_history: {
    status: CaseStatus
    changed_at: string
    remark: string | null
    resolution_reason: ResolutionReason | null
  }[]
}

/**
 * `serialize_submission`. `access_token` is present only for an anonymous
 * report, is returned exactly once, and is never stored server-side in a
 * recoverable form — only its SHA-256 reaches the database.
 */
export interface ReportSubmissionResult extends ReportSummary {
  access_token?: string
  access_token_notice?: string
}

export interface Paginated<T> {
  items: T[]
  pagination: { total: number; limit: number; offset: number; returned: number }
}

/**
 * Case status — `core.report.current_status`, the enum from `0001`. This is
 * the *case* lifecycle: triage, investigation, outcome. It is a different
 * state machine from dispatch (`DispatchState` below), which tracks one
 * physical emergency response and can be closed while the case underneath it
 * is still under review.
 */
export type CaseStatus =
  | 'submitted'
  | 'triaged'
  | 'under_review'
  | 'action_taken'
  | 'resolved'
  | 'closed_no_action'
  | 'duplicate'
  | 'withdrawn'

export const STATUS_LABELS: Record<CaseStatus, string> = {
  submitted: 'Received',
  triaged: 'Triaged',
  under_review: 'Under review',
  action_taken: 'Action taken',
  resolved: 'Resolved',
  closed_no_action: 'Closed',
  duplicate: 'Linked to another report',
  withdrawn: 'Withdrawn',
}

/** The legal graph, mirroring `CASE_TRANSITIONS` in `case_service.py` exactly.
 *  Used only to decide which controls to offer — the frontend is never the
 *  enforcement layer, and the server re-checks every one of these. */
export const CASE_TRANSITIONS: Record<CaseStatus, CaseStatus[]> = {
  submitted: ['triaged', 'withdrawn', 'duplicate', 'closed_no_action'],
  triaged: ['under_review', 'withdrawn', 'duplicate', 'closed_no_action'],
  under_review: ['action_taken', 'resolved', 'withdrawn', 'duplicate', 'closed_no_action'],
  action_taken: ['resolved', 'closed_no_action'],
  resolved: [],
  closed_no_action: [],
  duplicate: [],
  withdrawn: [],
}

export const TERMINAL_STATUSES: ReadonlySet<CaseStatus> = new Set([
  'resolved',
  'closed_no_action',
  'duplicate',
  'withdrawn',
])

export const REQUIRES_ASSIGNMENT: ReadonlySet<CaseStatus> = new Set([
  'under_review',
  'action_taken',
  'resolved',
])

export type ResolutionReason =
  | 'action_taken'
  | 'no_action_warranted'
  | 'insufficient_information'
  | 'referred_elsewhere'
  | 'duplicate_of_existing_case'
  | 'withdrawn_by_reporter'
  | 'other'

export const RESOLUTION_REASON_LABELS: Record<ResolutionReason, string> = {
  action_taken: 'Action was taken',
  no_action_warranted: 'No action warranted',
  insufficient_information: 'Insufficient information to proceed',
  referred_elsewhere: 'Referred elsewhere',
  duplicate_of_existing_case: 'Duplicate of an existing case',
  withdrawn_by_reporter: 'Reporter withdrew the report',
  other: 'Other',
}

/** Mirrors `RESOLUTION_REASONS_BY_STATUS` in `case_service.py`. */
export const RESOLUTION_REASONS_BY_STATUS: Partial<Record<CaseStatus, ResolutionReason[]>> = {
  resolved: ['action_taken', 'referred_elsewhere', 'other'],
  closed_no_action: [
    'no_action_warranted',
    'insufficient_information',
    'referred_elsewhere',
    'other',
  ],
  duplicate: ['duplicate_of_existing_case', 'other'],
  withdrawn: ['withdrawn_by_reporter', 'other'],
}

export interface CaseStatusEntry {
  history_id: number
  from_status: CaseStatus | null
  to_status: CaseStatus
  changed_at: string
  remark: string | null
  visible_to_reporter: boolean
  resolution_reason: ResolutionReason | null
}

/** A staff member, named safely — a role and a label, never a bare id. */
export interface ResponderLabel {
  role: UserRole
  label: string
}

export interface CaseAssignment {
  assignment_id: number
  assigned_at: string
  assignee: ResponderLabel
  assigned_by: ResponderLabel | null
  note: string | null
}

export const RELATIONSHIP_LABELS: Record<ReporterRelationship, string> = {
  affected: 'This happened to me',
  witness: 'I saw it happen',
  third_party: 'I am reporting on behalf of someone else',
}

interface ItemsResponse<T> {
  items: T[]
}

/**
 * What POST /evidence returns.
 *
 * `upload_token` is an opaque capability, not a storage path. The client never
 * learns where the image lives — a client that cannot name a path cannot ask
 * the server to serve one it does not own.
 */
export interface EvidenceUploadResult {
  upload_token: string
  content_type: string
  byte_size: number
  width: number
  height: number
  notice: string
}

export interface EvidenceLimits {
  max_bytes: number
  accepted_types: string[]
  max_per_report: number
}

/* -------------------------------------------------------------------------- */
/* Responder plane                                                            */
/* -------------------------------------------------------------------------- */

export type DispatchState =
  'pending' | 'acknowledged' | 'dispatched' | 'on_scene' | 'stood_down' | 'closed'

/**
 * How a corroborating signal compared with the campus location the student
 * selected.
 *
 * Descriptive only. None of these ever changes where the incident is — the
 * selected location is the operational truth, and a photograph's GPS is a
 * signal a human weighs.
 */
export type LocationResolution = 'corroborated' | 'approximate' | 'conflicting' | 'unresolved'

export interface LocationSignal {
  resolution: LocationResolution
  source: 'photo_exif' | 'device_gps' | 'location_default'
  distance_m: number | null
  signal_captured_at: string | null
  note: string | null
}

export interface DispatchRecord {
  dispatch_id: string
  state: DispatchState
  raised_at: string
  acknowledged_at: string | null
  dispatched_at: string | null
  on_scene_at: string | null
  closed_at: string | null
  responder_note: string | null
}

export interface IncidentSummary {
  public_ref: string
  report_kind: ReportKind
  submission_mode: SubmissionMode
  category: { category_id: number; code: string; label: string } | null
  location: {
    location_id: number
    code: string
    name: string
    latitude: number | null
    longitude: number | null
    is_mapped: boolean
    is_synthetic: boolean
  }
  location_hint: string | null
  occurred_at: string
  submitted_at: string
  is_emergency: boolean
  is_ongoing: boolean
  status: CaseStatus
  reporter_contactable: boolean
  evidence_count: number
  location_signal: LocationSignal | null
  dispatch: DispatchRecord | null
  /** Whether the case currently has an owner. Who, specifically, is a detail-
   *  view question (`IncidentDetail.assignment`); the queue only needs this. */
  is_assigned: boolean
}

/**
 * Where a responder is being sent.
 *
 * `latitude`/`longitude` are null until the campus survey lands, and
 * `is_mapped` says so rather than leaving a client to infer it from two nulls.
 * `dispatch_note` and `location_hint` are what make the destination usable in
 * the meantime — the last hundred metres are a door, not a pin.
 */
export interface Destination {
  location_id: number
  code: string
  name: string
  latitude: number | null
  longitude: number | null
  is_mapped: boolean
  navigable: boolean
  location_type: string | null
  is_indoor: boolean | null
  dispatch_note: string | null
  zone_name: string | null
  location_hint: string | null
  /** True only for a demo/development fixture — never a real surveyed destination. */
  is_synthetic: boolean
}

/**
 * Machine assistance on one incident.
 *
 * `model_trained_on_real_data` is always present and is the field the interface
 * must read before framing anything as a finding. Every model this project can
 * currently produce is trained on generated text.
 *
 * The full probability distribution is deliberately absent — a responder needs
 * the suggestion and its confidence, not sixteen numbers.
 */
export interface Triage {
  suggested_category: { category_id: number; code: string; label: string } | null
  confidence: number | null
  agrees_with_reporter: boolean | null
  overridden_category: { category_id: number; code: string; label: string } | null
  model: string | null
  model_trained_on_real_data: boolean
  risk_score: number | null
  risk_band: 'low' | 'moderate' | 'high' | 'critical' | null
  risk_factors: Record<string, unknown> | null
  related: RelatedIncident[]
}

export interface RelatedIncident {
  link_id: number
  public_ref: string
  link_type: 'duplicate' | 'related' | 'same_pattern'
  similarity: number | null
  review_state: 'unreviewed' | 'confirmed' | 'rejected'
  occurred_at: string
  location_name: string
  category_label: string | null
}

export const LINK_TYPE_LABELS: Record<RelatedIncident['link_type'], string> = {
  duplicate: 'May be the same incident',
  related: 'May be related',
  same_pattern: 'Similar to a recurring problem',
}

export const RISK_BAND_LABELS: Record<'low' | 'moderate' | 'high' | 'critical', string> = {
  low: 'Low',
  moderate: 'Moderate',
  high: 'High',
  critical: 'Critical',
}

export interface IncidentDetail extends IncidentSummary {
  destination: Destination
  narrative: string | null
  narrative_available: boolean
  narrative_withheld_reason?: string
  /** Opaque ids. Each resolves only through an authorised streaming request. */
  evidence: { evidence_id: string }[]
  reporter_contact_guidance: string
  triage: Triage | null
  /** Full history, unfiltered — the responder view. Distinct from what the
   *  same report shows its own reporter via `ReportDetail.status_history`. */
  case_status_history: CaseStatusEntry[]
  assignment: CaseAssignment | null
}

export interface IncidentQueue extends Paginated<IncidentSummary> {
  /** Whether any listed incident has a surveyed coordinate to draw. */
  mapping_available: boolean
}

export const DISPATCH_LABELS: Record<DispatchState, string> = {
  pending: 'Awaiting response',
  acknowledged: 'Acknowledged',
  dispatched: 'On the way',
  on_scene: 'On scene',
  stood_down: 'Stood down',
  closed: 'Closed',
}

/** What the responder does next, per state. Mirrors DISPATCH_TRANSITIONS. */
export const DISPATCH_NEXT: Record<DispatchState, { state: DispatchState; label: string }[]> =
  {
    pending: [
      { state: 'acknowledged', label: 'Acknowledge' },
      { state: 'stood_down', label: 'Stand down' },
    ],
    acknowledged: [
      { state: 'dispatched', label: 'On the way' },
      { state: 'stood_down', label: 'Stand down' },
    ],
    dispatched: [
      { state: 'on_scene', label: 'Arrived on scene' },
      { state: 'stood_down', label: 'Stand down' },
    ],
    on_scene: [{ state: 'closed', label: 'Close' }],
    stood_down: [],
    closed: [],
  }

export const RESOLUTION_LABELS: Record<LocationResolution, string> = {
  corroborated: 'Photo location agrees with the selected place',
  approximate: 'Location as selected by the reporter',
  conflicting: 'Photo location differs from the selected place',
  unresolved: 'No mapped coordinate for this location',
}

export const ROLE_LABELS: Record<UserRole, string> = {
  student: 'Student',
  security: 'Campus security',
  icc: 'Internal Complaints Committee',
  admin: 'Administration',
}

export function createApi(client: ApiClient) {
  return {
    /** Create the application account behind an already-verified credential. */
    register: (email?: string) =>
      client.post<AccountIdentity>('/auth/register', email ? { email } : {}),

    /**
     * The caller's identity and role, from the database.
     *
     * The frontend asks rather than infers. A role held in browser state is a
     * claim the browser is making about itself.
     */
    me: () => client.get<AccountIdentity>('/auth/me'),

    /**
     * Set the caller's own display name. Refused server-side with a 403 for
     * a student account — this call exists for security/ICC/admin accounts
     * only, and the frontend should not offer it to a student in the first
     * place.
     */
    updateDisplayName: (displayName: string) =>
      client.patch<AccountIdentity>('/auth/me', { display_name: displayName }),

    /**
     * The caller's own notification inbox, most recent first. Polled rather
     * than pushed — no Firebase Cloud Messaging is configured, so this is the
     * whole delivery mechanism there is.
     */
    notifications: (limit = 20, offset = 0, unreadOnly = false) => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
      if (unreadOnly) params.set('unread_only', 'true')
      return client.get<NotificationsResponse>(`/notifications?${params.toString()}`)
    },

    /** Mark one of the caller's own notifications as read. */
    markNotificationRead: (notificationId: string) =>
      client.post<void>(`/notifications/${notificationId}/read`),

    locations: () =>
      client.get<ItemsResponse<CampusLocation>>('/locations').then((r) => r.items),

    categories: () =>
      client.get<ItemsResponse<ReportCategory>>('/categories').then((r) => r.items),

    /**
     * Submit a report — identified, anonymous, emergency, or anonymous
     * emergency. One endpoint for every shape, as the backend was designed:
     * a separate anonymous URL would itself be a signal, and a proxy log showing
     * which endpoint a student called would undo the anonymity it provided.
     */
    submitReport: (payload: unknown) =>
      client.post<ReportSubmissionResult>('/reports', payload),

    evidenceLimits: () => client.get<EvidenceLimits>('/evidence/config'),

    /**
     * Upload one image. The server validates the bytes, strips EXIF and GPS,
     * re-encodes, and stores the result; the token returned here is attached to
     * a report at submission.
     */
    uploadEvidence: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return client.post<EvidenceUploadResult>('/evidence', form)
    },

    /** Remove a staged upload before the report is submitted. */
    discardEvidence: (token: string) => client.delete<void>(`/evidence/${token}`),

    /**
     * The caller's own identified reports.
     *
     * Anonymous submissions are absent by construction — they are linked to
     * nobody, so no query starting from a user id can reach them.
     */
    myReports: (limit = 50, offset = 0) =>
      client.get<Paginated<ReportSummary>>(`/reports/mine?limit=${limit}&offset=${offset}`),

    /**
     * One of the caller's own reports, with its full (visible-to-reporter)
     * status timeline. The same endpoint an anonymous reporter's token also
     * reaches — this call just doesn't supply one.
     */
    report: (publicRef: string) => client.get<ReportDetail>(`/reports/${publicRef}`),

    /* ---------------------------------------------------------------- */
    /* Responder plane. Every call below is refused server-side for a    */
    /* student, whatever the browser believes about its own role.        */
    /* ---------------------------------------------------------------- */

    incidents: (limit = 100, offset = 0, statuses?: CaseStatus[]) => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
      if (statuses && statuses.length > 0) params.set('status', statuses.join(','))
      return client.get<IncidentQueue>(`/incidents?${params.toString()}`)
    },

    incident: (publicRef: string) => client.get<IncidentDetail>(`/incidents/${publicRef}`),

    raiseDispatch: (publicRef: string) =>
      client.post<DispatchRecord>(`/incidents/${publicRef}/dispatch`, {}),

    advanceDispatch: (publicRef: string, state: DispatchState, note?: string) =>
      client.post<DispatchRecord>(`/incidents/${publicRef}/dispatch/state`, { state, note }),

    /**
     * Fetch one evidence image as a Blob.
     *
     * Through the API client so the bearer token goes with it. There is no URL
     * that would work in an `<img src>` — by design: the server streams the
     * bytes after re-checking authorisation, and mints no signed or public URL
     * that could outlive that check or be forwarded to anyone.
     */
    evidenceImage: (evidenceId: string) => client.getBlob(`/evidence/${evidenceId}`),

    /**
     * Record that a responder judged the category differently from the model.
     * Does not change what the student chose — that stays on the report.
     */
    overrideCategory: (publicRef: string, categoryId: number) =>
      client.post<void>(`/incidents/${publicRef}/category`, { category_id: categoryId }),

    /** Confirm or reject a proposed link between two reports. */
    reviewLink: (publicRef: string, linkId: number, confirmed: boolean) =>
      client.post<void>(`/incidents/${publicRef}/links/${linkId}`, { confirmed }),

    /**
     * Move a case to its next legal status. `remark` is required by the server
     * for every transition except the first acknowledgement, and
     * `resolution_reason` exactly when `target` is terminal — both checked
     * again here isn't necessary; the server is the authority and returns a
     * clear 400 either way.
     */
    changeCaseStatus: (
      publicRef: string,
      target: CaseStatus,
      options: {
        remark?: string
        resolutionReason?: ResolutionReason
        visibleToReporter?: boolean
      } = {},
    ) =>
      client.post<CaseStatusEntry>(`/incidents/${publicRef}/status`, {
        target,
        remark: options.remark,
        resolution_reason: options.resolutionReason,
        visible_to_reporter: options.visibleToReporter ?? true,
      }),

    /** Claim a case (no id) or hand it to a named colleague (with one). */
    assignCase: (publicRef: string, assigneeUserId?: string, note?: string) =>
      client.post<CaseAssignment>(`/incidents/${publicRef}/assign`, {
        assignee_user_id: assigneeUserId,
        note,
      }),

    /** Release the active assignment. The case stays exactly where it is in
     *  the lifecycle and reappears in the unassigned queue. */
    unassignCase: (publicRef: string, note?: string) =>
      client.post<void>(`/incidents/${publicRef}/unassign`, { note }),
  }
}

export type Api = ReturnType<typeof createApi>
