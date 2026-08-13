# CampusShield — Production Readiness (Phase 4G)

**Status:** Engineering work verified locally, including a real Docker
build run under real gunicorn and a real disposable-database end-to-end
scenario. **Not verified: a real Firebase project, a real production
database, and real campus coordinates** — none exist in this environment,
and none are invented here. See §17 for the exact verdict and why.

This document is the deployment-facing index. It does not repeat what
`BACKEND_ARCHITECTURE.md`, `RESPONDER_ARCHITECTURE.md`, `DATABASE.md`,
`DATABASE_SETUP.md`, `CAMPUS_LOCATIONS.md`, and `PHASE_4F_FIREBASE_
STORAGE.md` already cover in depth — it cross-references them and adds
only what those documents don't: how to actually run this outside
localhost.

---

## 1. Current architecture

```
React/Vite SPA  ──HTTPS──▶  Flask API (gunicorn)  ──▶  PostgreSQL
      │                            │
      ├─ Firebase Auth (ID tokens) ├─ Firebase Admin SDK (token verify)
      └─ no direct Storage access  └─ Firebase Storage (evidence, private)
```

- Backend: `BACKEND_ARCHITECTURE.md` (auth, authorization, request
  lifecycle, configuration), `RESPONDER_ARCHITECTURE.md` (map, dispatch,
  evidence viewing), `CASE_LIFECYCLE.md`, `ML_INTEGRATION.md`.
- Schema: `DATABASE.md` (31 revisions), `DATABASE_SETUP.md` (local setup).
- Evidence/storage: `PHASE_4F_FIREBASE_STORAGE.md`.
- Campus map/location data: `CAMPUS_LOCATIONS.md`, `PHASE_5_CAMPUS_MAP.md`,
  `PHASE_5B_DEMO_CAMPUS_DATA.md`.
- Frontend: `frontend/README.md`.

Nothing in this phase changed that architecture. It closed the gap between
"correct in isolation, tested against real Postgres" and "has an actual
path to running outside a developer's machine."

## 2. Deployment architecture

No infrastructure existed before this phase (no Dockerfile, no CI, no
platform config of any kind — confirmed by a repository-wide search, not
assumed). The architecture below is chosen to fit what the codebase
actually is: a Flask API with no server-side rendering, no background-job
framework, and no dependency on anything beyond PostgreSQL and Firebase —
not chosen from a generic template.

| Component | Recommendation | Why |
|---|---|---|
| Frontend | Static hosting (Netlify, Cloudflare Pages, or Vercel) | Pure client-rendered Vite build; no server needed. `frontend/public/_redirects` (Netlify/Cloudflare) and `frontend/vercel.json` are both already in the repo. |
| Backend | Any container host that runs a Dockerfile (Render, Railway, Fly.io, a plain VM) | `backend/Dockerfile` builds and runs under gunicorn; nothing here needs a specific vendor's proprietary features. |
| Database | Managed PostgreSQL (the same host's managed offering, or RDS/Supabase/Neon) | Nothing about this schema needs a specific provider — triggers, CHECK constraints, and partial indexes are portable SQL. |
| Authentication | Firebase Authentication | Already the implemented and tested provider — see §4. |
| Evidence storage | Firebase Cloud Storage | Already the implemented and tested provider — see §6. |
| Scheduler | The platform's own scheduled-job feature, or cron on a small VM | Calls `backend/scripts/reap_and_purge_evidence.py` — see §8. Deliberately not a new scheduler service; see that script's own docstring. |
| CI | GitHub Actions (`.github/workflows/ci.yml`, added this phase) | Runs the real backend suite against a real Postgres service container, plus the full frontend gate, on every push/PR. |

No Kubernetes, no message queue, no cache layer is required by anything
this codebase does today.

## 3. Environment variables

Full, authoritative lists: `backend/.env.example` and `frontend/.env.example`.
New in this phase, not yet in earlier documentation:

| Variable | Default | Purpose |
|---|---|---|
| `RATELIMIT_STORAGE_URI` | `memory://` | Flask-Limiter's backend. `memory://` counts requests per **process** — correct with one gunicorn worker, independently-counted per worker otherwise. Point at Redis for a true shared limit; see §11. |
| `TRUST_PROXY_HEADERS` | `false` | Whether to trust `X-Forwarded-For` from the immediate upstream (`werkzeug.ProxyFix`, one hop). Enable only when exactly one reverse proxy/load balancer sits in front of this process. Wrong in either direction: off behind a real proxy means the rate limiter keys on the proxy's own address (every client shares one bucket); on with no proxy in front means a client can spoof the header and pick any address to be rate-limited as. |
| `RATELIMIT_ENABLED` | `true` (`false` only in `TestingConfig`) | Not meant to be set in `.env` — a code-level default, listed here for completeness. |

Every other variable (`DATABASE_URL`, `AUTH_PROVIDER`, `FIREBASE_*`,
`STORAGE_PROVIDER`, `CORS_ORIGINS`, the two peppers, `MAX_*`,
`PENDING_UPLOAD_TTL_HOURS`, `MODEL_ARTIFACT_PATH`) is unchanged from
earlier phases and already documented in `backend/.env.example`.

`ProductionConfig` refuses to start (not warns — refuses) with: debug mode,
`AUTH_PROVIDER=dev`, a wildcard or `localhost` CORS origin,
`STORAGE_PROVIDER=memory`, or a missing `AUDIT_IP_PEPPER`/`REPORT_TOKEN_
PEPPER`. This is enforced in `backend/app/config.py`, not merely
documented — confirmed by the existing `ProductionConfig` test suite.

## 4. Firebase setup — human action required

1. Create a Firebase project (console.firebase.google.com). No project
   exists for this application in this environment.
2. Enable Firebase Authentication with the sign-in method(s) the frontend
   will use.
3. Create a Firebase Storage bucket, keep it **private** (default rules
   already deny everything — see §6).
4. Generate a service-account key (Project settings → Service accounts →
   Generate new private key). Store it **outside this repository**;
   `.gitignore` already excludes `*serviceAccount*.json` and similar
   patterns as a second layer, not the only one.
5. Set, on the backend: `AUTH_PROVIDER=firebase`, `FIREBASE_PROJECT_ID`,
   one of `FIREBASE_CREDENTIALS_PATH` / `FIREBASE_CREDENTIALS_JSON` /
   `GOOGLE_APPLICATION_CREDENTIALS`, `STORAGE_PROVIDER=firebase`,
   `FIREBASE_STORAGE_BUCKET`.
6. Set, on the frontend build: the `VITE_FIREBASE_*` variables from
   `frontend/.env.example` (these are public client identifiers, not
   secrets — the frontend audit confirmed no secret-shaped value is ever
   read there).
7. Deploy `storage.rules` (§6).

**This step cannot be performed from this environment** — it requires a
Google account, console access, and creating real external resources. See
§17.

## 5. Database setup

Full local setup: `DATABASE_SETUP.md`. For a production instance:

1. Provision managed PostgreSQL 13+ (native `gen_random_uuid()`; nothing
   here needs a specific vendor extension).
2. Set `DATABASE_URL` to it (`postgresql+psycopg://...`, with
   `?sslmode=require` for a managed host over the public internet).
3. Apply migrations **once, explicitly**, not as a side effect of the
   application container starting:
   ```
   DATABASE_URL=<production-url> alembic upgrade head
   ```
4. `sql/roles_and_grants.sql` and `sql/seed_report_categories.sql`
   (repository root) are the one-time, hand-run SQL this project has
   always used for role/grant setup and the report-category vocabulary —
   unchanged by this phase.

## 6. Storage setup

Full design: `PHASE_4F_FIREBASE_STORAGE.md`. For production:

1. `storage.rules` (repository root) denies every client request — deploy
   it before the bucket carries anything real:
   ```
   firebase deploy --only storage --project <your-firebase-project-id>
   ```
   Not independently validated by the Firebase CLI in this environment
   (`npx firebase-tools` failed on a local permissions error unrelated to
   the rules themselves) — the syntax follows Firebase's documented
   deny-all template; running `firebase deploy` is itself the real
   validation, and it has not happened here.
2. The Admin SDK bypasses these rules by design — they are a second,
   independent guarantee against the bucket being left at Firebase's
   30-day test-mode default, not the primary access control. Flask's own
   authorization (`can_view_report`) is the real one.

## 7. Campus-location import

Full policy: `CAMPUS_LOCATIONS.md`. Mechanically:

```
python backend/scripts/import_campus_locations.py --locations <survey.csv> [--zones <zones.csv>] --commit
```

Dry-run by default (omit `--commit` to preview). Validates ranges,
duplicate codes/coordinates/names, and the verified/active schema rules
before writing anything — see the script's own docstring for the full
list.

**Never run `scripts/seed_demo_campus_locations.py` against a production
database.** It refuses a database name containing `prod` or `live`, but
that is a backstop, not a guarantee for every possible production
database name — treat "never run it outside local development" as the
actual rule, the name check as a second layer. Demo/synthetic locations
are schema-flagged (`is_synthetic`) and visibly marked everywhere the
frontend renders them, but they are still not real data and have no place
in a production database at all.

**Zero verified real coordinates exist anywhere in this codebase**, by
design (`CAMPUS_LOCATIONS.md` §1). Production deployment does not require
them to exist first — the map has an honest empty state, and reports
still work; corroboration and mapping simply activate location by
location as each is actually surveyed.

## 8. Scheduler

`backend/scripts/reap_and_purge_evidence.py` needs to run on an interval;
nothing in the application calls it. It is not a scheduler itself — see
its own docstring for why one was not built.

**Cron** (a small always-on VM or the platform's persistent-process
offering):
```cron
0 * * * *   cd /srv/backend && python scripts/reap_and_purge_evidence.py >> /var/log/campusshield-cleanup.log 2>&1
```

**A managed scheduled-job feature** (Render Cron Jobs, Railway Cron,
Fly.io Machines on a schedule, GCP Cloud Scheduler hitting a Cloud Run
job): point it at the same command, same environment variables as the API
container, running hourly. Verify with:
```
python scripts/reap_and_purge_evidence.py --dry-run
```
which reports counts without deleting or purging anything — the
verification command asked for in §21 of this phase's own directive.

## 9. Backend deployment

```
docker build -f backend/Dockerfile -t campusshield-backend .
docker run -p 5000:5000 \
  -e DATABASE_URL=... -e AUTH_PROVIDER=firebase -e FIREBASE_PROJECT_ID=... \
  -e STORAGE_PROVIDER=firebase -e FIREBASE_STORAGE_BUCKET=... \
  -e AUDIT_IP_PEPPER=... -e REPORT_TOKEN_PEPPER=... -e CORS_ORIGINS=https://your-frontend \
  campusshield-backend
```

Genuinely built and run in this phase, not only written: the image built
cleanly, served real HTTP through gunicorn with two workers, returned the
correct 200/503 liveness/readiness split, and correctly enforced the
per-route rate limits under a single worker (see §11 for what the
two-worker case demonstrated about the default rate-limit storage).
Migrations are **not** run by the container's own startup — see §5.

## 10. Frontend deployment

```
cd frontend && npm ci && npm run build
```
Deploy `dist/` to the chosen static host, with `VITE_API_BASE_URL` and the
`VITE_FIREBASE_*` variables set **at build time** (Vite bakes them into
the bundle — there is no runtime configuration step). `frontend/public/
_redirects` and `frontend/vercel.json` (both added this phase) give
Netlify/Cloudflare Pages and Vercel respectively the SPA fallback rule a
client-side-routed app needs; other hosts (nginx, S3+CloudFront) need the
equivalent `try_files`/error-document rule configured on the host itself,
not from a repository file.

## 11. Security checklist

| Item | Status |
|---|---|
| Debug mode refused in production | Done — `ProductionConfig` |
| Dev auth refused in production | Done — `ProductionConfig` |
| Wildcard/localhost CORS refused | Done — `ProductionConfig` |
| In-memory storage refused in production | Done — `ProductionConfig` |
| Required secrets enforced at boot | Done — `ProductionConfig` |
| No stack traces/internal errors leaked | Done — `app/errors.py`, verified by audit |
| No secrets in logs | Done — verified by audit across all sampled log call sites |
| Global security headers (`X-Frame-Options`, CSP, HSTS, `Referrer-Policy`) | Done this phase — `app/security/response_headers.py` |
| Request-rate abuse controls | Done this phase — `app/security/rate_limit.py`; see the storage-backend caveat below |
| CSRF | Not applicable — pure bearer-token API, confirmed no cookies/sessions anywhere |
| Firebase Storage bucket private | Rules written this phase (`storage.rules`); not yet deployed to a real project |
| **Rate limiter storage in a multi-worker deployment** | **Human action**: the default `memory://` backend is per-process. Empirically confirmed in this phase — 32 requests against a 30-per-minute limit tripped correctly with one gunicorn worker and did not with two, because each worker kept its own counter. Set `RATELIMIT_STORAGE_URI` to Redis before running more than one worker, or accept a per-worker limit as the deployed behaviour. |
| **`TRUST_PROXY_HEADERS`** | **Human action**: must be set correctly for the real deployment topology (see §3) or the rate limiter keys on the wrong address. |

## 12. Backup and restore

Not previously documented anywhere in this repository.

**Database.** Standard `pg_dump`/`pg_restore`, or the managed host's own
point-in-time recovery if it offers one:
```
pg_dump --format=custom "$DATABASE_URL" > backup.dump
pg_restore --clean --if-exists --dbname="$DATABASE_URL" backup.dump
```
Take one before every production migration.

**Evidence bytes.** A database backup does **not** include Firebase
Storage objects — `evidence.evidence_object.storage_path` rows point at
bytes that live entirely outside PostgreSQL. Enable Firebase Storage's own
object versioning or scheduled bucket export if evidence-bytes durability
independent of the database is required; nothing in this codebase manages
that today.

**What a restore changes.** Restoring an older database backup can bring
back `evidence_object` rows whose `storage_path` no longer resolves (if
`purge_expired()` ran, or objects were deleted, after the backup was
taken) or, conversely, can leave orphaned bytes in the bucket with no
database row pointing at them. Neither is a new problem this phase
introduced; both are inherent to keeping bytes and metadata in two
systems, and are worth knowing about before relying on a restore.

## 13. Smoke tests

Manual, after every deployment:

1. Landing page loads
2. Login (Firebase) succeeds
3. Registration provisions an account
4. Dashboard renders for a signed-in user
5. Report creation form loads
6. Location map renders (or shows its honest empty state if nothing is
   surveyed yet)
7. Evidence upload accepts an image and shows it staged
8. Submission returns a confirmation
9. My Reports shows the new report
10. Responder login succeeds for a staff account
11. Incident map renders (or its empty state)
12. Evidence viewer opens a submitted image
13. Navigation link is present and points somewhere sensible
14. A case status transition succeeds and the reporter's own view reflects it
15. Logout clears the session and protected routes redirect to login

## 14. Rollback

**Application.** Redeploy the previous image tag / previous static-site
build. Both the backend Docker image and the frontend build are
immutable, versioned artifacts once built — no in-place mutation to undo.

**Migration.** Alembic supports `downgrade`, and every migration in this
repository (`0001`–`0006`) has been exercised upgrade → downgrade → upgrade
against a disposable database as part of its own phase's verification.
That is not the same guarantee as "downgrading in production loses
nothing": a downgrade after real data has been written under the newer
schema can lose that data (for example, `0006`'s downgrade stops computing
`retention_expires_at` for new rows but does not attempt to un-write
values already computed for existing ones — the column survives, only the
trigger's behaviour reverts). Back up before downgrading a production
database, not only before upgrading it.

## 15. Known limitations

Consolidated from this phase's own audit; each already exists in more
detail in the document named:

- **Zero verified real campus coordinates** — `CAMPUS_LOCATIONS.md`.
  Deployable without them; mapping/corroboration activate as each location
  is surveyed.
- **Real Firebase Storage unverified** — `PHASE_4F_FIREBASE_STORAGE.md`.
  No credentials in this environment; the provider and its wiring are
  verified as far as that allows.
- **ML suggestions are trained on synthetic data only** —
  `ML_INTEGRATION.md`. Disclosed to the responder in the UI itself, not
  only in documentation.
- **No colleague-picker UI for case assignment** — `CASE_LIFECYCLE.md` §9.
  Backend fully supports assigning to a named colleague; no frontend
  control exists for it.
- **No case reopening, no notification on status change** —
  `CASE_LIFECYCLE.md` §9. A reporter learns of updates only by revisiting
  their report.
- **Rate limiting is per-process by default** — §11 above.
- **No load balancer/reverse-proxy topology decided yet** — `TRUST_PROXY_
  HEADERS` needs a real answer once one is (§3).
- **CI is written and reasoned through but not run** — no GitHub remote
  exists in this environment to actually execute `.github/workflows/ci.yml`
  against; its correctness rests on the same reasoning as everything else
  in this phase (cross-checking `tests/conftest.py`'s actual subprocess
  calls, matching every environment variable it depends on), not on a
  real green run.
- **Backup/restore is documented, not automated** — §12. No scheduled
  backup job exists; the commands are correct but manual.

## 16. Human actions required

Nothing in this list can be completed by an engineering session against
this repository — each requires an external account, real infrastructure,
or physical presence:

1. Create the Firebase project, enable Authentication, create the Storage
   bucket, generate and secure a service-account credential (§4).
2. Deploy `storage.rules` via the real Firebase CLI and confirm it
   compiles and applies (§6) — not merely that its syntax matches the
   documented template.
3. Provision production PostgreSQL and set `DATABASE_URL` (§5).
4. Choose and provision the actual hosting platform for the backend
   container and the frontend static build (§2).
5. Perform the campus location survey and import it (§7) — the one
   product-completeness gap that is not a code gap at all.
6. Decide the real deployment topology (single worker vs. multi-worker
   with Redis; reverse proxy or direct exposure) and set
   `RATELIMIT_STORAGE_URI`/`TRUST_PROXY_HEADERS` to match (§3, §11).
7. Wire the external scheduler for `reap_and_purge_evidence.py` (§8).
8. Generate and store `AUDIT_IP_PEPPER`/`REPORT_TOKEN_PEPPER` as real
   secrets (`python3 -c "import secrets; print(secrets.token_hex(32))"`),
   not the placeholder values used in this repository's own tests.
9. Configure the real production domain, TLS, and `CORS_ORIGINS` to match
   it.
10. Create the first real responder accounts (security/ICC/admin roles) —
    `scripts/seed_dev_data.py` is explicitly local-only.
11. Run the manual smoke test (§13) against the real deployment once all
    of the above is in place.
12. Push this repository to a real Git remote and let `.github/workflows/
    ci.yml` actually run, to convert "reasoned through" into "observed
    passing" (§15).

## 17. Final verdict

**READY EXCEPT FOR EXTERNAL SETUP.**

Every engineering item this phase's audit found — missing security
headers, missing rate limiting, missing production WSGI server, missing
deployment artifacts, missing SPA fallback routing, missing top-level
error boundary, the Firebase Storage app-reuse defect (Phase 4F), the
never-invoked evidence retention purge (Phase 4F) — is fixed and verified:
by the full backend suite (554 passing), the full frontend suite (333
passing), a real `docker build` and real `docker run` under gunicorn
answering real HTTP with the right headers and the right rate-limit
behaviour, and an extended real end-to-end scenario against a disposable
PostgreSQL database covering report submission, EXIF/GPS handling, ML
triage, the full case lifecycle, the emergency dispatch lifecycle, and
every access-control and anonymity guarantee this product makes.

It is not called **READY FOR DEPLOYMENT** because that would claim
something not actually true: no Firebase project, no production database,
and no real campus coordinates exist anywhere this phase could reach.
Those are the items in §16, none of which is an engineering gap — every
one requires a human with account access, infrastructure budget, or a
physical campus to stand in.
