# CampusShield — Backend Architecture

**Phases 1–2.** Flask + SQLAlchemy + PostgreSQL, with Firebase Authentication.
Foundation, one complete vertical slice — report submission and retrieval, end to
end — and production authentication.

Schema design is [DATABASE.md](DATABASE.md); migrations are
[DATABASE_SETUP.md](DATABASE_SETUP.md). This document is the application.

---

## 1. What is built

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/v1/health` | none | Liveness |
| `GET /api/v1/health/db` | none | Readiness: connectivity, version, migration revision, schemas |
| `GET /api/v1/locations` | required | Active campus locations |
| `GET /api/v1/categories` | required | Report categories |
| `POST /api/v1/reports` | required | Submit — identified, anonymous, emergency, or anonymous emergency |
| `GET /api/v1/reports/mine` | required | The caller's own identified reports |
| `GET /api/v1/reports/<public_ref>` | optional | One report, if the caller is entitled to it |
| `GET /api/v1/evidence/config` | required | Upload limits, so the browser and server cannot disagree |
| `POST /api/v1/evidence` | required | Upload one image; returns a single-use capability token |
| `DELETE /api/v1/evidence/<upload_token>` | required | Discard a staged upload before it is attached |
| `GET /api/v1/evidence/<evidence_id>` | required | Stream one attached image, re-authorised per request |
| `GET /api/v1/incidents` | authority | Responder queue, scoped by category routing |
| `GET /api/v1/incidents/<public_ref>` | authority | Incident detail: destination, evidence ids, location signal |
| `POST /api/v1/incidents/<public_ref>/dispatch` | authority | Raise a dispatch (emergency reports only) |
| `POST /api/v1/incidents/<public_ref>/dispatch/state` | authority | Acknowledge / en route / on scene / close / stand down |
| `POST /api/v1/incidents/<public_ref>/category` | authority | Record a category judgement differing from the model's suggestion |
| `POST /api/v1/incidents/<public_ref>/links/<link_id>` | authority | Confirm or reject a proposed link between two reports |
| `GET /api/v1/reports/<public_ref>` (reused) | optional | Now also returns `status_history` and `resolution_reason` for a reporter's own report |
| `POST /api/v1/incidents/<public_ref>/status` | authority | Move the *case* through `submitted → … → resolved`. Distinct from dispatch — see §13 |
| `POST /api/v1/incidents/<public_ref>/assign` | authority | Claim a case (no body) or assign it to a named colleague |
| `POST /api/v1/incidents/<public_ref>/unassign` | authority | Release the active assignment; the case stays where it is in the lifecycle |
| `GET /api/v1/auth/me` (reused) | required | Now also returns `display_name` — `null` for a student |
| `PATCH /api/v1/auth/me` | required | Set the caller's own `display_name`; refused for a student with 403 |
| `GET /api/v1/notifications` | required | The caller's own notification inbox, paginated, with `unread_count` |
| `POST /api/v1/notifications/<id>/read` | required | Mark one of the caller's own notifications read |

Authentication is Firebase (`AUTH_PROVIDER=firebase`), with the development
provider retained for tests and local work.

Deliberately absent: push notifications (in-app notifications are built — see
§14), intervention tracking, clustering, and hotspot analytics. The
authorisation policy for authority access exists and is enforced on `GET
/reports/<ref>`; the dashboard that lists reports comes later.

Phase 4D added the case lifecycle: status transitions and assignment. See §13
below. Phase 4E added in-app notifications and the one editable account
field. See §14.

---

## 2. Folder structure

```
backend/
├── app/
│   ├── __init__.py          application factory — assembly only
│   ├── config.py            environment-driven config; production refuses unsafe values
│   ├── extensions.py        db instance + declarative Base
│   ├── errors.py            error types and the single JSON error shape
│   ├── routes/              HTTP layer — parse, delegate, serialise, commit
│   │   ├── dependencies.py  service construction from the request session
│   │   ├── health.py  catalog.py  reports.py  evidence.py  incidents.py
│   ├── services/            business logic — no Flask imports
│   │   ├── report_service.py  catalog_service.py  health_service.py
│   │   ├── evidence_service.py  upload → sanitise → store → stage → attach
│   │   ├── incident_service.py  responder queue, destination, dispatch states
│   │   ├── triage_service.py    classify, embed, relate, score — never decides
│   │   ├── risk_scorer.py       rule-based triage ordering; no reporter input
│   │   ├── location_service.py  corroborated / approximate / conflicting / unresolved
│   ├── repositories/        every database query lives here
│   │   ├── report_repository.py  catalog_repository.py  evidence_repository.py
│   │   ├── incident_repository.py  never joins report_attribution, by design
│   │   ├── ml_repository.py     classifications, embeddings, links, risk
│   │   ├── audit_repository.py   health_repository.py
│   ├── models/              SQLAlchemy mapping onto the Alembic-owned schema
│   │   ├── enums.py  app_user.py  campus.py  report.py  evidence.py  policy.py
│   ├── schemas/             marshmallow request validation + response serializers
│   │   ├── requests.py  responses.py
│   ├── ml/                  serving the trained model — no research imports
│   │   ├── artifact.py      bundle loading; refuses an artifact with no provenance
│   │   ├── text.py          normalisation, pickled inside the artifact
│   ├── storage/             object storage — the ONLY place that knows a bucket exists
│   │   ├── provider.py      StorageProvider Protocol: put / get / delete / exists
│   │   ├── firebase_provider.py  the only firebase_admin.storage import
│   │   ├── memory_provider.py    in-process; development and tests
│   │   ├── paths.py         server-generated object paths
│   ├── security/            authentication and authorisation
│   │   ├── principal.py     what an authenticated caller is
│   │   ├── providers.py     dev + Firebase, behind one Protocol
│   │   ├── firebase.py      ID token verification — only firebase_admin import
│   │   ├── context.py       request-scoped auth; the only Flask import here
│   │   └── authorization.py the access policy, as pure functions
│   └── utils/               correlation.py  references.py  campus_time.py
├── scripts/
│   ├── seed_dev_data.py     dev accounts; never creates campus locations
│   └── smoke_test.py        end-to-end checks against a running server
├── tests/                   155 tests against a real PostgreSQL database
├── run.py                   development entry point
└── pyproject.toml           ruff, mypy, pytest configuration
```

### The dependency rule

```
routes  →  services  →  repositories  →  models  →  PostgreSQL
   ↓          ↓
schemas    security
```

Dependencies point one way only. `services/` and `repositories/` import no
Flask; the only Flask import in `security/` is `context.py`, which parses the
request. That is not a stylistic preference — it means the authorisation policy
can be tested as pure functions (25 unit tests, no database, no app), and it
means swapping the authentication provider cannot change how requests are
parsed.

**A route never makes an access decision.** It asks
`app.security.authorization`, and that module is small enough to read end to end
in one sitting. The moment a route starts deciding, the policy has to be audited
in two places.

---

## 3. Request lifecycle

```
HTTP request
   │
   ├─ before_request: assign correlation id ────────── utils/correlation.py
   ├─ before_request: clear cached principal ───────── security/context.py
   │
   ├─ route: @authenticated / @optional_authentication
   │     ├─ AuthProvider.extract_credential(headers) → str | None
   │     └─ AuthProvider.authenticate(credential)    → Principal | None
   │           └─ firebase: TokenVerifier.verify() → uid → identity.app_user
   │
   ├─ route: schema.load(request.json)  ───────────── schemas/requests.py
   │     └─ unknown fields rejected; forbidden fields named explicitly
   │
   ├─ route: service.method(principal, validated)  ── services/
   │     ├─ business rules (emergency eligibility, quota, future timestamps)
   │     ├─ authorisation policy call ─────────────── security/authorization.py
   │     └─ repository calls  ─────────────────────── repositories/
   │           └─ SQLAlchemy → PostgreSQL
   │                 └─ triggers + CHECK constraints as the backstop
   │
   ├─ route: audit write  ─────────────────────────── repositories/audit_repository.py
   ├─ route: db.session.commit()
   ├─ route: serialize_*(result)  ─────────────────── schemas/responses.py
   │
   └─ after_request: X-Request-ID header
```

**The commit is in the route, not the service.** A service method is one unit of
work; letting it commit would make composing two of them into one transaction
impossible. The route owns the transaction boundary because the route is what
knows a request has finished.

On any exception: the error handler rolls the session back, maps the exception
to the standard JSON shape, and returns. `teardown_appcontext` removes the
session so a dirty one is never inherited by the next request.

---

## 4. Authentication abstraction

The whole contract is two methods:

```python
class AuthProvider(Protocol):
    name: str
    def extract_credential(self, headers: Mapping[str, str]) -> str | None: ...
    def authenticate(self, credential: str | None) -> Principal | None: ...
```

`None` from either means "no credential was presented" — an anonymous caller on
a public endpoint. Raising `AuthenticationError` means "a credential was
presented and it is bad". The distinction matters: the first is normal, the
second is worth logging.

Providers receive a **header mapping, not a Flask request**, so they stay
testable without an application context. Each owns its own wire format, because
the formats genuinely differ — `X-Dev-User` versus `Authorization: Bearer` — and
that also means neither provider will read the other's header.

`Principal` is what everything downstream sees:

```python
@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    role: UserRole
    email: str
    is_active: bool = True
```

Note the absences. No token, no claims dictionary, no provider handle. If a
service could reach the raw credential, something would eventually re-verify or
forward it and the abstraction would leak. Its `__repr__` omits the email,
because reprs reach logs and tracebacks and a principal is attached to every
request.

### Development provider

`AUTH_PROVIDER=dev` trusts the `X-Dev-User` header — a user id, `firebase_uid`,
or email — with **no verification whatsoever**. Whoever sends the header becomes
that user. That is the point: it lets the API be exercised before Firebase
exists.

Two things keep it contained. It resolves against the real `identity.app_user`
table rather than fabricating principals, so role checks and authorisation are
exercised exactly as they will be in production. And `ProductionConfig.validate()`
**refuses to start** when `AUTH_PROVIDER=dev`, so it cannot reach production by
someone forgetting an environment variable.

### One bug worth recording

The principal is cached in `flask.g` for the duration of a request. `g` lives on
the *application* context, not the request context, and Flask reuses an existing
application context rather than pushing a new one when it finds one active.
Without an explicit reset, a principal cached by one request survives into the
next and **the second caller is authenticated as the first**.

The test suite caught it: a student's `/reports/mine` returned another student's
report. It surfaced under test because the fixture holds one app context across
several requests, but it is a genuine cross-user authentication bug, not a test
artefact — any embedding that pushes its own context would hit it. The fix is a
`before_request` hook that clears the cached principal, so the credential is
resolved exactly once per request and the possibility is gone.

---

## 5. Firebase Authentication

```
React  →  Firebase Auth  →  ID token
       →  Authorization: Bearer <token>
       →  FirebaseAuthProvider          app/security/providers.py
       →  FirebaseTokenVerifier         app/security/firebase.py   ← only firebase_admin import
       →  identity.app_user lookup      role comes from HERE
       →  Principal
       →  existing authorisation policy (unchanged)
       →  PostgreSQL
```

### The verification seam

`app/security/firebase.py` is the **only** module that imports `firebase_admin`.
Everything above it works against `TokenVerifier`:

```python
class TokenVerifier(Protocol):
    def verify(self, token: str) -> VerifiedSubject: ...
```

That boundary does two jobs. It lets the provider's own logic be tested
exhaustively without credentials, and it means swapping identity provider again
would touch one file.

`VerifiedSubject` carries `uid`, `email`, `email_verified` — **and no claims
dictionary**. A raw claims bag is an invitation to read `claims["role"]`, so
there is deliberately nothing to read. A test asserts the field set.

### Where role comes from

**`identity.app_user`, never the token.** A Firebase custom claim would be the
obvious alternative and is wrong twice over: a token already issued keeps
whatever it was minted with, so a role change takes up to an hour to bite; and a
leaked token carries its privileges with it rather than being a key looked up
against a table someone can revoke. The identity provider says who you are; the
database says what you may do.

Two tests hold this: one that an ICC member's UID yields `UserRole.ICC` from the
row, and one that changing the row changes the principal on the very next
request.

### Error taxonomy

| Condition | Status | Code |
|---|---|---|
| No `Authorization` header | 401 | `UNAUTHENTICATED` |
| Wrong scheme, empty or spaced token | 401 | `MALFORMED_AUTHORIZATION` |
| Signature, audience or issuer invalid | 401 | `TOKEN_INVALID` |
| Expired | 401 | `TOKEN_EXPIRED` |
| Revoked | 401 | `TOKEN_REVOKED` |
| Firebase account disabled | 401 | `ACCOUNT_DISABLED` |
| Verified UID with no local account | 401 | `UNAUTHENTICATED` |
| Google's certificates unreachable | **503** | `SERVICE_UNAVAILABLE` |

Two of those rows are decisions rather than mechanics. **Expired is distinguished
from invalid** because a client that cannot tell them apart cannot know to
refresh and retry — it signs the user out instead. And **unreachable certificates
are 503, not 401**: telling a legitimate user their credentials are bad, when the
truth is we cannot reach Google, sends them to reset a password that was never
the problem.

A verified UID with no local account is deliberately *not* distinguished from a
bad token, so that anyone holding any valid token for the project cannot
enumerate which UIDs have accounts here.

### Credential validation at startup

Verifying an ID token needs the **project id**, not the service-account key — the
SDK checks the signature against Google's public certificates. Credentials are
for privileged Admin operations, of which `FIREBASE_CHECK_REVOKED=true` is the
only one this code performs.

So startup validates what it can cheaply and skips what it does not need:

- **Missing `FIREBASE_PROJECT_ID`** → refuses to start. Left to fail later it
  surfaces as every login returning 401, which reads like a credential problem
  and sends whoever is debugging it to the wrong place.
- **A configured but unreadable credential file, or malformed inline JSON** →
  refuses to start. Cheap to check, and a wrong path is a typo worth catching.
- **Application Default Credentials with `check_revoked` off** → not probed.
  Off Google infrastructure, resolution blocks on the metadata server at
  169.254.169.254 until it times out — measured at 9.2 seconds. A test asserts
  startup stays under two.
- **ADC with `check_revoked` on** → probed and fatal, because that path genuinely
  needs it.

### What is dev-only, and how it stays that way

`AUTH_PROVIDER=dev` trusts `X-Dev-User` with no verification at all. Four things
contain it: `ProductionConfig.validate()` refuses to start with it selected;
`build_provider` logs `DEVELOPMENT AUTHENTICATION ENABLED` on every boot; each
provider reads only its own header, so a bearer token cannot be smuggled past the
dev provider or a dev header past Firebase; and it resolves against the real user
table, so roles and authorisation behave exactly as they will in production.

### What did not change

Every route, service, repository, response schema, and the entire authorisation
policy. None of them has ever seen a token, and none of them was edited for this
phase. The changes are confined to `app/security/`, `app/config.py`, and one line
of `app/__init__.py`.

---

## 6. Authorisation model

Pure functions over an explicit `ReportAccessContext`, assembled by the
repository so the policy issues no queries of its own and can be unit-tested
without a database.

```python
@dataclass(frozen=True, slots=True)
class ReportAccessContext:
    report_id: uuid.UUID
    routes_to_role: UserRole | None
    requires_confidentiality: bool
    reporter_user_id: uuid.UUID | None   # None for every anonymous report
    assigned_to_user_id: uuid.UUID | None
    accessed_via_token: bool = False
```

`reporter_user_id` is `None` for anonymous reports because no attribution row
exists — the policy cannot leak an identity it is never given.

### The rules

|  | `can_view_report` | `can_view_narrative` | `can_resolve_identity` |
|---|---|---|---|
| Reporter | yes | yes | — |
| Token holder | yes | yes | n/a |
| Other student | **no** | **no** | no |
| Assigned officer | yes | yes | no |
| ICC, report routed to ICC | yes | yes | **yes** |
| Security, report routed to security | yes | yes (non-confidential) | no |
| Security, report routed to ICC | **no** | **no** | no |
| Admin | yes | **no** | no |

Two rules produce that table.

**Being an authority is not access.** A role alone never grants sight of a
report; the report's category has to route to that role. Anything else is "give
everyone all reports" wearing a role check. `routes_to_role = None` (an
uncategorised report) grants nothing — the policy fails closed.

**Metadata and narrative are separate decisions.** Administration sees report
metadata for analytics and sees no narratives, mirroring the database grants
where `cs_analytics` is denied `SELECT` on `core.report_narrative` outright. The
pattern layer without the accounts.

`can_resolve_identity` has no endpoint in Phase 1. It is defined now so the rule
exists before it is needed rather than being invented under deadline, and any
future caller must also write to `audit.identity_disclosure_log`.

---

## 7. Privacy boundaries

Every guarantee is enforced at two levels: the application refuses first so the
failure is a clean, testable error, and the database refuses regardless so an
application bug cannot break it.

| Guarantee | Application | Database |
|---|---|---|
| Anonymous reports have no identity | Service never calls `attribute()` for anonymous | `trg_attribution_requires_identified` |
| Anonymity cannot be undone | `submission_mode` not settable by clients | `trg_report_mode_immutable` |
| Anonymous reporters are uncontactable | Never set directly; derived from consent | `ck_report_anonymous_is_not_contactable` |
| Evidence carries no filename, for any report | Repository never supplies one | `trg_evidence_anonymous_no_filename` |
| Clients cannot choose a storage path | `storage/paths.py` generates it; no request field is read | — |
| Uploads are not linked to an uploader | `pending_upload` has no user column to write | — |
| An upload token works once | `attach()` deletes the staging row in the same transaction | `UNIQUE(token_hash)` |
| Stored images carry no metadata | Sanitiser rebuilds the image from raw pixels | — |
| EXIF GPS never becomes the incident location | Resolver returns a verdict; `location_id` is never rewritten | — |
| Responders cannot reach a reporter's identity | `IncidentRepository` has no join to `report_attribution` | `trg_attribution_requires_identified` |
| Only emergencies are dispatched | Service refuses first, with a stated reason | `trg_dispatch_requires_emergency` |
| Analytics cannot read a precise coordinate | Separate table, `cs_analytics` not granted | Table-level grant |
| A model never changes a student's category | Suggestion and override live on `ml.report_classification` | — |
| Risk scoring cannot read a reporter | `score_report()` takes no principal or attribution | `ck_risk_factors_no_credibility_terms` |
| A proposed link never widens access | Filtered against what the caller may already open | — |
| Embeddings never leave the server | No serialiser reads `ml.report_embedding` | — |
| Retention terms are fixed at collection | Application never supplies them | `trg_narrative_retention_stamp` + immutability trigger |
| Audit is append-only | Repository has no update path | Triggers + `INSERT`-only grant |

### `user_id` never leaves

`schemas/responses.py` names every field explicitly. There is no `dump()` of an
ORM object anywhere, so adding a column to a model cannot widen an API response
— which is exactly how identity leaks happen. Tests assert, for every role, that
no response body contains `user_id`, the reporter's UUID, or their email.

Also withheld: `storage_path` (an internal bucket location), `has_cctv` and
`has_lighting` (published openly, "no camera here, unlit after dark" is a map of
where not to be seen), and `routes_to_role` / `base_severity` (a student
choosing a category should not be choosing an audience).

### 404, never 403

`GET /reports/<ref>` returns the identical 404 for "no such report" and "exists
but not yours". A 403 confirms the reference is real, and references are printed
on acknowledgements and appear in screenshots — the endpoint would become an
oracle for whether a code exists. A test asserts the two responses are
indistinguishable.

### Anonymous reports are absent from `/reports/mine`

Not a gap. They are linked to nobody, so no query starting from a user id can
reach them — including this one. If this endpoint could find them, so could
anything else with the same access. Anonymous reporters follow their case with
the one-time token, which is returned once at submission and stored only as a
SHA-256 hash.

### Rate limiting without linking

`identity.submission_quota` records how many reports a user filed on a date,
never which ones. The rate limit works; the deanonymisation does not exist.

---

## 8. Database access pattern

**Repositories own every query.** Services call repositories; routes call
services. No service builds a `select()`, and no route touches `db.session`
except to commit.

Models map onto the schema Alembic created. `db.create_all()` is never called —
the schema includes triggers, partial unique indexes, and CHECK constraints
SQLAlchemy cannot express, and letting the ORM create tables would produce a
database that looks right and enforces nothing.

Two mapping decisions carry meaning:

- **`Report` has no relationship to `AppUser`.** Not a lazy one, not a nullable
  one. There is nothing on the model to leak.
- **`Report` does not lazy-load its narrative.** `get_narrative()` is a separate
  repository call, so every caller passes an authorisation check first rather
  than tripping over a convenient property.

Models inherit the plain `Base` rather than `db.Model`, which keeps them free of
Flask and lets mypy check them.

### Session lifecycle

One session per request, from Flask-SQLAlchemy's scoped session.
`teardown_appcontext` rolls back on exception and always removes it. Tests bind
the session to an open transaction with
`join_transaction_mode="create_savepoint"`, so a request's `commit()` becomes a
savepoint release and the fixture can still roll everything back — the suite is
order-independent and leaves nothing behind.

---

## 9. Error handling

One shape, from every failure path:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request body failed validation.",
    "details": { "fields": { "category_id": ["No such active category."] } },
    "request_id": "9f8c...-...-..."
  }
}
```

`request_id` is also on the `X-Request-ID` header, so a student can quote it from
a screenshot and it can be found in the logs. A client-supplied id is honoured if
it matches `^[A-Za-z0-9._-]{8,64}$` so a trace can span frontend and backend —
validated, because an unvalidated echo of client input into log files is how log
injection works.

| Code | Status | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Body failed validation |
| `MALFORMED_REQUEST` | 400 | Not parseable as JSON |
| `UNAUTHENTICATED` | 401 | No or bad credential |
| `FORBIDDEN` | 403 | Authenticated, not permitted |
| `NOT_FOUND` | 404 | Does not exist, **or is not yours** |
| `CONFLICT` | 409 | Conflicts with current state |
| `IMMUTABLE_STATE` | 409 | Attempt to change something declared immutable |
| `QUOTA_EXCEEDED` | 429 | Daily submission limit |
| `SERVICE_UNAVAILABLE` | 503 | Database unreachable |
| `INTERNAL_ERROR` | 500 | Unhandled |

`IMMUTABLE_STATE` is separate from `CONFLICT` on purpose: changing a report's
submission mode, or attaching an identity to an anonymous report, is not an
ordinary conflict — it is an attempt to undo an anonymity guarantee, and it
should read that way in a log.

Two rules about what errors say. **500s are opaque** — an internal message can
disclose table names, query fragments, or file paths; the request id is the
bridge to the logs. And **known database constraint names are mapped to 400-level
errors and logged at WARNING**, because a constraint firing means a request got
past a service check that should have caught it. The schema caught it; the
service still has a gap.

---

## 10. Testing strategy

**155 tests, all passing.** 39 carry a `privacy` marker: a failure there is not
an ordinary test failure.

| File | Tests | Scope |
|---|---|---|
| `test_health.py` | 7 | Liveness, readiness, correlation ids |
| `test_catalog.py` | 8 | Locations, categories, field-level exposure |
| `test_report_creation.py` | 39 | All four report shapes, validation, immutable fields |
| `test_report_access.py` | 19 | Authorisation, cross-student isolation, token access |
| `test_security_unit.py` | 25 | Policy and auth abstraction — no database, no Flask |
| `test_seed_integrity.py` | 11 | Seed data cannot invent a campus location |
| `test_firebase_auth.py` | 46 | Bearer extraction, token failures, UID mapping, role source |

### Against a real PostgreSQL database

Not negotiable for this project. Most of what is being tested — the anonymity
trigger, the append-only audit tables, the CHECK keeping anonymous reports
uncontactable — exists **only in PostgreSQL**. Against SQLite or mocks, every one
of those tests would pass while the guarantee was absent, which is worse than not
testing them.

The session fixture creates the database, runs `alembic upgrade head` as a
subprocess (the same command a developer runs, so a broken migration fails here
rather than confusingly later), and drops it afterwards. Each test runs in a
transaction that is rolled back.

### What the privacy tests assert

Beyond "the service does not create attribution", one test bypasses the
application entirely and inserts directly into `identity.report_attribution` for
an anonymous report, asserting the database raises `anonymity violation`. If the
service layer were ever refactored wrongly, that test still fails.

Others assert: no response for any role contains a `user_id`, an email, or a
storage path; the 404 for "not yours" is indistinguishable from "does not exist";
an anonymous report is unreachable even by the account that submitted it; a token
for one report does not open another; and admin gets metadata with
`narrative_available: false`.

### Seed integrity

`core.campus_location` starts and stays empty. `scripts/seed_dev_data.py` creates
accounts and no locations, because a location with invented coordinates does not
fail loudly — it produces a map pin in the wrong place, a hotspot where nobody
was, and an impact measurement against a baseline that never existed. Wrong data
that looks right is worse than none, because none is visibly missing.

`test_seed_integrity.py` enforces it two ways: behaviourally, by running the seed
script against a scratch database and asserting the table is still empty; and
structurally, by asserting the source contains no insert into `campus_location`
and no coordinate-shaped literal. The second matters because a regression will
most likely arrive as someone pasting a plausible pair of numbers, and that gets
caught in review rather than after it has produced a hotspot nobody can explain.

Test *fixtures* do create synthetic locations, and that is a different thing:
they live in a rolled-back transaction inside a database that is dropped when the
session ends, and never reach a development or production database. Seed data
does.

### Smoke test

`scripts/smoke_test.py` drives a running server over real HTTP, covering the same
guarantees at the network boundary. The pytest suite proves the logic; this proves
it serves. With no active location it skips report submission and says why —
because no location is currently the correct state, not a broken setup.

### Tooling

`ruff format` (45 files), `ruff check` (E, F, W, I, N, UP, B, C4, SIM, RUF), and
`mypy` on `app/` — all clean. mypy is deliberately not `--strict`: the value is
catching real type errors in services and repositories, not annotating every
Flask view signature.

---

## 11. Configuration

All from environment variables; see `backend/.env.example`.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | psycopg 3 URL; a bare `postgresql://` is rewritten |
| `TEST_DATABASE_URL` | Recreated per test run |
| `FLASK_ENV` | `development` / `testing` / `production` |
| `AUTH_PROVIDER` | `dev` or `firebase` |
| `CAMPUS_TIMEZONE` | Derives `occurred_hour` / `occurred_dow` |
| `AUDIT_IP_PEPPER` | HMAC key for `audit.access_log.ip_hash` |
| `REPORT_TOKEN_PEPPER` | Reserved |
| `MAX_EVIDENCE_PER_REPORT` | Default 5 |

`ProductionConfig.validate()` refuses to start on `AUTH_PROVIDER=dev` or missing
peppers. A backend that boots with development authentication enabled in
production is worse than one that does not boot.

Without `AUDIT_IP_PEPPER`, client addresses are **dropped rather than stored
unkeyed** — a bare SHA-256 of an IPv4 address is reversible by enumerating the
whole space, so an unkeyed "hash" is not anonymisation.

### Why the campus timezone is configuration

`occurred_hour` and `occurred_dow` are plain columns, not generated ones:
`EXTRACT(... FROM timestamptz)` is not `IMMUTABLE` in PostgreSQL and cannot back
a generated column. The application owns the conversion and must use the campus
zone, not the server's — a report filed at 21:00 on campus is an evening report
wherever the server sits. Hotspot detection and the night-hours risk factor read
these columns, so a wrong zone skews every downstream pattern quietly rather than
failing loudly.

---

## 12. What later phases add, and where

Case status transitions and assignment (Phase 4D) are no longer in this table
— they are built. See §13. Evidence reaping/purging (Phase 4F) likewise —
see §15.4. What remains for evidence is only the external cron/scheduler
trigger for `scripts/reap_and_purge_evidence.py`, not application code.

| Feature | Where it goes | What already exists |
|---|---|---|
| Identity disclosure | `services/disclosure_service.py` | `can_resolve_identity`, the audit table |
| Hotspots and the map | `services/analytics_service.py` | `analytics.hotspot`, the k=3 view |
| Intervention tracking | `services/intervention_service.py` | All four intervention tables |

The pattern for each: a repository for queries, a service for rules, a route for
HTTP, explicit serializers, and an authorisation function if it touches a report.

---

## 13. Case lifecycle (Phase 4D)

`services/case_service.py`, backed by `repositories/case_repository.py`.

### Case status vs. dispatch status — two state machines, deliberately

`core.report.current_status` (`submitted → triaged → under_review →
action_taken → resolved`, plus the terminal exits `closed_no_action`,
`duplicate`, `withdrawn`) and `core.emergency_dispatch.state` (`pending → … →
closed`) are independent. `CaseService` never reads or writes
`core.emergency_dispatch`; `IncidentService`'s dispatch methods never read or
write case status. A case can reach `resolved` with no dispatch ever raised — most
reports are not emergencies. A dispatch can be `closed` while the case is still
`under_review` — arriving and helping is not the same as concluding the
institutional process. Closing one never implies closing the other.

### The legal graph

`CaseService.CASE_TRANSITIONS`, enforced server-side on every request — the
frontend's copy of the same graph (`CASE_TRANSITIONS` in `lib/api.ts`) only
decides which buttons to show and is never trusted:

```
submitted ──▶ triaged ──▶ under_review ──┬──▶ action_taken ──▶ resolved
    │             │             │        └──────────────────────▲
    ├─▶ withdrawn ┤             ├─▶ resolved
    ├─▶ duplicate ┤             ├─▶ closed_no_action
    └─▶ closed_no_action        ├─▶ withdrawn
                                 └─▶ duplicate
```

`under_review`, `action_taken`, and `resolved` additionally require an active
`core.case_assignment` row *at the moment of the transition*
(`REQUIRES_ACTIVE_ASSIGNMENT`). Every transition beyond the first
acknowledgement (`submitted → triaged`) requires a `remark`. Every terminal
transition requires a `resolution_reason` from a controlled seven-value
vocabulary, and the specific reason must fit the target status
(`RESOLUTION_REASONS_BY_STATUS`) — both checked in the service and again by
`ck_case_status_resolution_reason_terminal` in the database.

### Authorization: `can_manage_case`

Narrower than `can_view_report`. Excludes an anonymous reporter's own access
token (proves "this is my report," never "I am staff"), excludes `admin` (the
narrative firewall's reasoning again: oversight, not operation), and admits
exactly the routed role or the report's current assignee. Illegal-transition
and unauthorised-actor attempts both surface as a stated `ConflictError` or the
same 404-not-403 pattern every other responder-plane endpoint already used.

### Assignment

`core.case_assignment`'s partial unique index (`WHERE is_active`) guarantees at
most one active owner per report at the database level; `CaseService.assign`
still releases the old row before creating the new one rather than depending on
the constraint to catch a bug. Assigning to a named colleague (rather than
self-assigning) is validated against the report's routing role before it
reaches the assignment-target-role trigger, so a mismatch reads as a clear 400
rather than a confusing 500.

### What a responder sees, what a reporter sees

`IncidentDetail` (responder) bundles the **full**, unfiltered
`case_status_history` and the current `assignment`. `ReportDetail` (reporter,
via `GET /reports/<ref>`, unchanged endpoint) carries only the subset with
`visible_to_reporter = true` — the same filter `ReportRepository.
visible_status_history` has applied since Phase 1, now exercised for the first
time.

---

## 14. Notifications and account display name (Phase 4E)

`services/notification_service.py` and `services/account_service.py`
(extended), backed by `repositories/notification_repository.py`. Full design
rationale, including the privacy reasoning, is [DATABASE.md](DATABASE.md)
§28 — this section covers the application-layer shape.

### Notifications are a side effect of `CaseService`, not a parallel write path

`NotificationService` has no route of its own that creates a notification.
`CaseService.change_status` and `CaseService.assign` each call it, optionally
— the dependency is injected as `notifications: NotificationService | None`,
the same optional-dependency pattern `ReportService` already uses for
`triage`. A `CaseService` built without one (most unit tests) behaves exactly
as before this phase; wiring it in `routes/dependencies.py::case_service()`
is what makes it live in the running application. This keeps "does the state
machine transition correctly" and "does a transition notify the right person"
as separable questions, tested separately.

### Honest delivery state

`DeliveryState.SENT` is stamped at creation and means "written and readable
through `GET /notifications`," never "delivered to a device." No Firebase
Cloud Messaging credentials exist in this deployment's configuration; `notify.
device_token` is never read, and `Notification.fcm_message_id` is never set.
Both `delivery_state` and `fcm_message_id` are omitted from `serialize_
notification` for the same reason `DATABASE.md` gives: the response a client
receives must not be readable as a delivery guarantee that was not made.

### Two endpoints, the same oracle-avoidance pattern as everything else

`GET /notifications` is scoped to `recipient_user_id = <caller>` at the query
level — there is no admin or cross-user view, because nothing in this phase
needs one. `POST /notifications/<id>/read` returns 404 for someone else's
notification, identically to a nonexistent one, matching reports and
evidence. Neither writes to `audit.access_log`; see DATABASE.md §28.2 for why.

### `PATCH /auth/me` — the account-identity boundary, not the report-attribution one

`AccountService.update_display_name` checks `principal.role is UserRole.
STUDENT` and refuses with `AuthorizationError` (403) before touching the
database — the CHECK constraint on `identity.app_user` remains the backstop,
not the primary defence, matching this codebase's general pattern of
catching a rule in the service layer and only falling back to the database's
own enforcement if that is bypassed. This endpoint touches **account
identity** (`identity.app_user.display_name`) only. It has no reach into
**report attribution** (`identity.report_attribution`) or **report display**
(what a responder sees on an incident) — those remain three separate
questions this codebase keeps separate on purpose, and nothing here merges
them.

`Principal` gained a `display_name: str | None` field so the three call
sites that construct one (`DevAuthProvider.authenticate`,
`FirebaseAuthProvider.authenticate` via `_UserLookupMixin._principal_for`,
`AccountService.provision`) all populate it from the same column, and
`serialize_principal` returns it on every `GET /auth/me` and `PATCH
/auth/me` response.

---

## 15. Evidence storage — Firebase Cloud Storage made to actually work (Phase 4F)

The upload/sanitisation/authorization pipeline (`app/services/
evidence_service.py`, `app/utils/image_sanitizer.py`, `app/routes/
evidence.py`) was already complete and already tested against
`InMemoryStorageProvider`. This phase's job was narrower: make the real
`FirebaseStorageProvider` genuinely work when wired into the running
application, not just correct in isolation.

```
POST /evidence  →  validate → sanitise → generate_storage_path()
                →  StorageProvider.put()        ← FirebaseStorageProvider or
                →  EvidenceRepository.add_pending    InMemoryStorageProvider,
                                                       chosen by STORAGE_PROVIDER
GET /evidence/<id>  →  can_view_report()  →  StorageProvider.get()  →  stream
```

Nothing about that pipeline changed. What follows is what was found and
fixed underneath it.

### 15.1 The defect: storage never reused the app auth had already built

`FirebaseStorageProvider.__init__(self, *, bucket_name, app=None)` accepts
an `app` parameter specifically so it can reuse the named Firebase Admin
app (`campusshield-<project id>`) that `FirebaseTokenVerifier.initialize()`
creates — its own docstring says so: "one credential configuration, not
two." The actual wiring in `_build_storage_provider`
(`app/__init__.py`) never passed it:

```python
# before — app defaults to None
return FirebaseStorageProvider(bucket_name=app.config["FIREBASE_STORAGE_BUCKET"])
```

`firebase_admin.storage.bucket(name, app=None)` resolves against the SDK's
**global default app** — the one slot this codebase deliberately never
initialises, because `FirebaseTokenVerifier.initialize()` always registers
a *named* app instead, exactly so two Flask components cannot fight over
the default. In any real deployment with `STORAGE_PROVIDER=firebase`, the
first storage operation would have raised `ValueError: The default Firebase
app does not exist.` This had never been exercised — no Firebase
credentials have existed in any environment this project has run in — so
nothing had ever surfaced it.

### 15.2 The fix

`FirebaseTokenVerifier` gained a public `.app` property (initialises on
first access, idempotent). `_build_storage_provider` now does:

```python
verifier = build_verifier(app.config)
return FirebaseStorageProvider(
    bucket_name=app.config["FIREBASE_STORAGE_BUCKET"], app=verifier.app
)
```

`build_verifier(config)` constructs a `FirebaseTokenVerifier` from
`FIREBASE_PROJECT_ID` regardless of what `AUTH_PROVIDER` is set to, and
`.app` calls `.initialize()`, which does `firebase_admin.get_app(name)`
before ever calling `initialize_app` — a **process-global**, name-keyed
lookup. That single property covers both real configurations correctly:

- **`AUTH_PROVIDER=firebase` and `STORAGE_PROVIDER=firebase`** — the auth
  provider's own `build_provider()` call already initialised the named app
  first; storage's `build_verifier(...).app` resolves the *identical*
  object via `get_app(name)` rather than creating a second one. Verified by
  object identity (`storage_provider._app is auth_provider._verifier._app`)
  in `tests/test_storage_providers.py`, against the real `firebase_admin`
  SDK — nothing about app initialisation is mocked in that test, only the
  bucket lookup a real upload would eventually make.
- **`AUTH_PROVIDER=dev` and `STORAGE_PROVIDER=firebase`** — a legitimate
  combination (real bucket, no Firebase Auth) — storage initialises the
  named app itself. Also verified by a real, unmocked `create_app()` call.

`Config._validate_storage_provider` now also requires `FIREBASE_PROJECT_ID`
whenever `STORAGE_PROVIDER=firebase`, independent of `AUTH_PROVIDER` — it
was previously only required when `AUTH_PROVIDER=firebase`, which left the
dev-auth-plus-real-storage combination able to start and fail on first use
instead of at boot.

### 15.3 What was already correct, confirmed rather than assumed

- **Storage path opacity.** The client never supplies any path component —
  only a `content_type` reaches `generate_storage_path()`. Path traversal is
  not filtered, it is structurally unreachable. `tests/
  test_storage_providers.py` adds direct unit coverage of
  `is_server_generated()` against traversal-shaped strings, on top of the
  existing API-level test that a client-supplied `storage_path` field is
  rejected outright.
- **No public URL.** `StorageProvider` has no `public_url()` method by
  design; `tests/test_storage_providers.py::
  test_provider_has_no_public_url_method` makes that a structural
  assertion, not just an absence someone might not notice regressing.
- **Cleanup idempotency.** `FirebaseStorageProvider.delete()` already
  treated a missing object as success rather than raising. New unit tests
  exercise this directly (a fake bucket that raises `LookupError` on
  delete) rather than only through the reaper's own integration test.
- **No path logged alongside a storage exception.** The upload path logs
  only `type(exc).__name__`, never the exception text — which could echo a
  full object URL. Verified with `caplog` against a fake exception whose
  message deliberately contains a plausible bucket URL and query string.

### 15.4 Evidence retention now has a purge, not just a stamp

`EvidenceService.reap_expired()` (abandoned, never-attached uploads) has
existed since Phase 4B-1 but was never invoked anywhere. It now has a
sibling, `purge_expired()` (attached evidence past retention), and both are
invoked by a new script:

```
backend/scripts/reap_and_purge_evidence.py [--dry-run]
```

Full schema-side detail — the trigger change that makes
`retention_expires_at` a real value instead of permanent `NULL` — is
[DATABASE.md](DATABASE.md) §31. The service-layer shape:

```python
def purge_expired(self, *, limit=500, reason="retention_expiry") -> int:
    for evidence in self._evidence.expired_evidence(limit=limit):
        self._storage.delete(evidence.storage_path)      # idempotent
        self._evidence.mark_purged(evidence.evidence_id, reason=reason)
```

The row survives, marked `is_purged` — `get_evidence` and
`list_for_report` already filtered on that column (it existed for this
purpose since Phase 4B-1; nothing previously set it to `true`). Only the
bytes and the ability to view them go away. `reason` is constrained to
`ck_evidence_purge_reason`'s three values by the database regardless of
what a caller passes.

**This is explicitly not a scheduler**, matching this phase's own
instruction not to build one: `reap_and_purge_evidence.py` is a manual or
cron-invoked entry point, documented as such in its own docstring and in
`PHASE_4F_FIREBASE_STORAGE.md`. A production deployment needs an external
trigger for it. §12's table row for "Scheduled reaping of abandoned
uploads" is removed below — the capability now exists; only the schedule
itself remains a deployment concern, not a code gap.

### 15.5 Firebase Storage Security Rules

`storage.rules` (repository root) denies every client request
unconditionally. The Admin SDK — the only thing `FirebaseStorageProvider`
ever uses — bypasses Storage Security Rules entirely, so this is not what
makes evidence private; Flask's own authorisation check is. It exists as an
independent second guarantee: a bucket left at Firebase's own test-mode
default (`allow read, write: if true`, expiring after 30 days) is publicly
reachable by anyone with the bucket name, regardless of what the backend
does. `firebase.json` points the Firebase CLI at it —
`firebase deploy --only storage --project <id>`.

### 15.6 What remains genuinely unverified

No Firebase credentials exist in this environment — confirmed by checking
`FIREBASE_PROJECT_ID`, `FIREBASE_CREDENTIALS_PATH`, `FIREBASE_CREDENTIALS_
JSON`, and `GOOGLE_APPLICATION_CREDENTIALS` in both the shell environment
and `backend/.env`. `FirebaseStorageProvider` has been exercised against a
real, unmocked `firebase_admin` SDK for app initialisation, and against a
faked network boundary (`firebase_admin.storage.bucket`) for `put` / `get`
/ `delete` / `exists`. It has **not** been exercised against a real Firebase
Storage bucket — no bytes have been uploaded to, or downloaded from, an
actual Google-hosted bucket by this codebase. See
`PHASE_4F_FIREBASE_STORAGE.md` for what a real verification run would need
to do and confirm.

---
