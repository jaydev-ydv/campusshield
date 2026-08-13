# CampusShield — Production Deployment Runbook (Phase 4H)

This is the step-by-step operator runbook: what to actually type, in order,
to take a fresh checkout of this repository to a running production
deployment. For *why* each decision was made, see `PRODUCTION_READINESS.md`
(Phase 4G) — this document does not repeat that reasoning, only the exact
commands and the order they go in.

**Nothing in this document was executed against real external
infrastructure.** No Firebase project, no cloud hosting account, and no
Git remote exist in the environment this was written in — confirmed
directly (`env`, cloud CLI checks, `~/.aws`/`~/.config/gcloud`/etc. all
absent) immediately before writing this, not assumed from an earlier
phase. Every command below is real and has been reasoned through against
the actual codebase; the commands that require external credentials have
not been run. See §20.

---

## 1. Architecture

```
React/Vite SPA  ──HTTPS──▶  Flask API (gunicorn, 1 worker)  ──▶  PostgreSQL
      │                            │
      ├─ Firebase Auth             ├─ Firebase Admin SDK (token verify)
      └─ no direct Storage access  └─ Firebase Storage (evidence, private)
```

One gunicorn worker by default (`backend/Dockerfile`) — sized for a
demonstration/evaluation deployment, and it is what makes the default
in-process rate-limit storage exact rather than approximate. See §18.

## 2. Prerequisites

- A Firebase project (§4).
- A PostgreSQL 13+ instance reachable from wherever the backend runs (§5).
- A container host that can run a Dockerfile (any of: Render, Railway,
  Fly.io, a plain VM with Docker) for the backend.
- A static host (Netlify, Cloudflare Pages, Vercel, or any host serving
  `frontend/dist/`) for the frontend.
- `docker`, `psql`/`createdb`/`dropdb` (PostgreSQL client), Python 3.12,
  and Node 20 on whatever machine drives the deployment.

## 3. Environment variables

Full reference: `backend/.env.example`, `frontend/.env.example`. The two
peppers and every `FIREBASE_*`/`STORAGE_PROVIDER`/`RATELIMIT_*` value must
be real, not the placeholder/test values this repository's own test suite
uses. Generate secrets with:
```
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 4. Firebase setup — HUMAN ACTION REQUIRED, not performed here

1. console.firebase.google.com → create project.
2. Authentication → enable the sign-in method(s) the frontend uses.
3. Storage → create a bucket, leave it private.
4. Project settings → Service accounts → Generate new private key. Store
   the JSON file **outside this repository**.
5. Note the project id, bucket name, and credential file path/contents —
   they feed directly into §3's environment variables.

## 5. PostgreSQL setup

1. Provision a PostgreSQL 13+ instance (managed, or self-hosted).
2. Create the application database and, if following this project's own
   convention (`DATABASE_SETUP.md`), an unprivileged application role via
   `sql/roles_and_grants.sql`.
3. Set `DATABASE_URL` (§3) to it, with `?sslmode=require` for anything
   reached over the public internet.

## 6. Migration procedure

```
cd /path/to/campusshield
DATABASE_URL=<production-url> alembic upgrade head
```
Run this **once, by hand, before the backend container's first start** —
not as part of the container's own startup command (`backend/Dockerfile`
deliberately does not run migrations). Verify:
```
DATABASE_URL=<production-url> alembic current
```
should print `0006 (head)`.

## 7. Backend deployment

```
docker build -f backend/Dockerfile -t campusshield-backend .
docker run -d -p 5000:5000 \
  -e DATABASE_URL=<production-url> \
  -e AUTH_PROVIDER=firebase \
  -e FIREBASE_PROJECT_ID=<from §4> \
  -e FIREBASE_CREDENTIALS_JSON='<service-account JSON, from §4>' \
  -e STORAGE_PROVIDER=firebase \
  -e FIREBASE_STORAGE_BUCKET=<from §4> \
  -e AUDIT_IP_PEPPER=<generated secret> \
  -e REPORT_TOKEN_PEPPER=<generated secret> \
  -e CORS_ORIGINS=https://<your-frontend-domain> \
  -e FLASK_ENV=production \
  campusshield-backend
```
Genuinely built and run this phase (image tag `campusshield-4h`, since
removed): the image built cleanly, ran as a non-root user
(`groupadd`/`useradd campusshield` in the Dockerfile, confirmed with
`docker exec ... whoami` → `campusshield`), served real HTTP through
gunicorn, returned the correct 200 liveness / 503 readiness split against
a **genuinely unreachable** database host, and leaked no hostname or
connection detail into the HTTP response (only into the server-side log,
where `request_id` now correctly appears — confirmed by reading the
container's own log output, not assumed).

## 8. Frontend deployment

```
cd frontend
VITE_API_BASE_URL=https://<your-backend-domain>/api/v1 \
VITE_FIREBASE_API_KEY=<from Firebase console> \
VITE_FIREBASE_AUTH_DOMAIN=<...> VITE_FIREBASE_PROJECT_ID=<...> \
VITE_FIREBASE_APP_ID=<...> \
npm ci && npm run build
```
Deploy `dist/` to the static host. `frontend/public/_redirects`
(Netlify/Cloudflare) and `frontend/vercel.json` (Vercel) are already in
the repository and ship inside `dist/` automatically — confirmed present
in a real build's output in Phase 4G. Any other static host needs the
equivalent SPA-fallback rule configured on the host itself.

## 9. CORS configuration

Set the backend's `CORS_ORIGINS` to the exact frontend origin(s),
comma-separated, `https://` only. `ProductionConfig` refuses to start with
a wildcard or a `localhost` entry — this is enforced in code
(`backend/app/config.py`), not only documented.

## 10. Domain/TLS

Point DNS at the chosen hosts. TLS termination is the hosting platform's
job in every architecture recommended here (all of Render/Railway/Fly/the
static hosts terminate TLS for you); nothing in this codebase performs its
own certificate handling, and none is needed as long as `CORS_ORIGINS` and
`VITE_API_BASE_URL` both use the final `https://` domains.

## 11. Scheduler

```cron
0 * * * *   cd /srv/backend && python scripts/reap_and_purge_evidence.py >> /var/log/campusshield-cleanup.log 2>&1
```
or the hosting platform's own scheduled-job feature, running the same
command with the same environment variables as the API container.
Verified this phase against real seeded data in a disposable database
(not merely against an empty one, as Phase 4F's own verification was): a
backdated pending upload was reaped, a backdated evidence object was
purged (row survives, marked `is_purged`/`purged_at`/`purge_reason =
'retention_expiry'`), a non-expired evidence object was left untouched,
and a second run reaped/purged zero — genuine idempotency, not assumed.

## 12. Backup and restore

```
pg_dump --format=custom "$DATABASE_URL" > backup.dump
pg_restore --clean --if-exists --dbname="$DATABASE_URL" backup.dump
```
**IMPLEMENTED**: the commands above, correct and ready to use.
**CONFIGURED**: nothing — no scheduled backup job exists.
**HUMAN ACTION REQUIRED**: set up a recurring backup (the hosting
platform's managed-Postgres backup feature is the simplest option) and
verify a restore at least once before relying on it. Firebase Storage
objects are not included in a database backup — see
`PRODUCTION_READINESS.md` §12 for what that implies.

## 13. CI/CD

`.github/workflows/ci.yml` (added Phase 4G) runs the real backend suite
against a real Postgres service container, plus the full frontend gate,
on every push/PR. **Not run in this environment**: there is no Git remote
here (`git remote -v` fails — this directory is not even a local git
repository). Its correctness rests on tracing through what it does against
the actual `tests/conftest.py` fixture behaviour (confirmed which
environment variables `dropdb`/`createdb` need to reach the service
container, for instance), not on an observed green run. Push this
repository to GitHub and the workflow will run automatically; that is the
exact human action remaining here.

## 14. Campus location import

```
python backend/scripts/import_campus_locations.py --locations <survey.csv> [--zones <zones.csv>] --commit
```
No real survey data exists anywhere in this codebase, and none was
invented for this phase. **Real campus coordinates require a human field
survey** — `CAMPUS_LOCATIONS.md` §7 describes the process. The system
remains fully demonstrable without it: `scripts/seed_demo_campus_
locations.py` (never run against production — see
`PRODUCTION_READINESS.md` §7) populates schema-flagged, visibly-labelled
synthetic locations for exactly this purpose.

## 15. First responder setup

`scripts/seed_dev_data.py` is explicitly local/development-only (its own
docstring says so) and must not run against production. For production,
insert the first security/ICC/admin accounts directly:
```sql
INSERT INTO identity.app_user (firebase_uid, role, institutional_email, display_name)
VALUES ('<real-firebase-uid>', 'security', 'officer@university.edu', 'Campus Security Desk');
```
The `firebase_uid` must belong to a real account created through Firebase
Authentication first (§4) — this table only ever grants a *role* to an
identity Firebase has already verified, never the other way around.

## 16. Smoke test

Manual, after every deployment — the same 15 items as
`PRODUCTION_READINESS.md` §13, plus the scenario-based verification this
phase performed against disposable infrastructure (§17 below covers what
was actually run and what a real deployment still needs).

## 17. Rollback procedure

**Application**: redeploy the previous image tag / previous static build —
both are immutable once built.
**Migration**: `alembic downgrade <revision>`, verified upgrade →
downgrade → upgrade for every migration in this repository against a
disposable database as part of each one's own phase. Back up (§12) before
downgrading a production database — a downgrade can lose data written
under the newer schema; see `PRODUCTION_READINESS.md` §14 for the specific
example.

## 18. Security checklist

Full table: `PRODUCTION_READINESS.md` §11. Reconfirmed this phase against
a freshly built image, not only re-read: security headers present on a
real HTTP response from a real gunicorn process; readiness endpoint leaks
no connection detail on a genuinely unreachable database (tested against
a nonexistent hostname, not merely an unconfigured one); the container
runs as a non-root user; rate limiting is single-worker-exact by default,
documented as a deliberate topology choice for a demonstration deployment
rather than an oversight (`backend/Dockerfile`'s own comment explains
this, and it is the same conclusion Phase 4G reached empirically: one
worker keeps the in-memory limiter's count exact, two or more workers keep
it real but per-worker).

## 19. Known limitations

Same list as `PRODUCTION_READINESS.md` §15, with one addition from this
phase: the rate-limiting topology decision (§18 above) is now explicit and
defaulted-to in the Dockerfile rather than left as an open question — a
deployment that genuinely needs more than one worker's worth of concurrent
throughput needs `RATELIMIT_STORAGE_URI` pointed at Redis at the same time
it raises `--workers`, not before and not after.

## 20. Human actions still required

Identical set to `PRODUCTION_READINESS.md` §16 — nothing changed that
list, because nothing in this phase had access to the credentials that
would let any of those items actually close:

1. Create the Firebase project and everything under it (§4).
2. Provision production PostgreSQL (§5).
3. Choose and provision the actual hosting platform (§7, §8).
4. Perform the campus location survey (§14).
5. Generate and store real secrets (§3).
6. Configure the real domain/TLS (§10).
7. Wire the external scheduler (§11).
8. Create the first real responder accounts (§15).
9. Set up recurring database backups (§12).
10. Push this repository to a real Git remote so CI actually runs (§13).

None of these can be completed from this environment — each needs an
external account, a budget decision, or a person physically present on
campus. Everything that could be verified without them has been, this
phase, against real Docker/gunicorn and real (disposable) PostgreSQL —
not merely re-asserted from the previous phase's report.
