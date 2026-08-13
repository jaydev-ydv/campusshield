# CampusShield — Production Firebase Storage + Real Evidence Pipeline (Phase 4F)

**Status:** The application-layer defect this phase exists to find and fix
is fixed and verified, against a real (though uncredentialed) Firebase
Admin SDK and against real PostgreSQL. **Real Firebase Storage — an actual
Google-hosted bucket — was not verified.** No Firebase credentials exist in
this environment. That is stated here in full, not softened; see §11.

**Scope:** one application-layer defect fixed (`app/__init__.py`,
`app/security/firebase.py`, `app/config.py`), one dormant capability
finished (evidence retention/purge — migration `0006`, `app/repositories/
evidence_repository.py`, `app/services/evidence_service.py`, a new CLI
script), 26 new provider-level tests, a small correction to a pre-existing
test fixture, and two new repository-root Firebase config files
(`storage.rules`, `firebase.json`). No new tables, no new storage
abstraction, no redesigned evidence contract, no frontend changes — the
inspection this phase opened with found the frontend evidence UX already
complete.

---

## 1. The instruction, and what it did and did not ask for

The directive's own framing was explicit: the evidence architecture
(`evidence.evidence_object`, `evidence.pending_upload`, the sanitisation
pipeline, the streamed-not-signed retrieval model) already existed and was
already tested — what had **not** been genuinely verified was Firebase
Storage specifically. The task was to move from "architecture exists" to
"real Firebase Storage works end to end," and, failing that (credentials
being unavailable, which was expected and confirmed), to do everything
short of that honestly and say plainly what remained unverified.

Explicitly forbidden: inventing a second storage abstraction, a second
evidence table, redesigning the report/evidence contract, building a
general notification/scheduler system, and expanding into anything outside
evidence/storage (notifications, analytics, new ML, public maps, automatic
dispatch, and so on).

## 2. Inspection findings, in the order they were found

1. `FirebaseStorageProvider` (`app/storage/firebase_provider.py`) was
   **not** a stub. `put` / `get` / `delete` / `exists` were all fully
   implemented against the pinned `firebase-admin` 7.5 / `google-cloud-
   storage` 3.13 versions, with correct exception wrapping and idempotent
   delete. What was missing was never inside this file.
2. `_build_storage_provider` (`app/__init__.py`) constructed
   `FirebaseStorageProvider(bucket_name=...)` without the `app=` keyword
   the class exists to accept. See §3 — this is the phase's central
   finding.
3. Storage path generation (`app/storage/paths.py`) was already correct
   and already structurally traversal-proof: the client supplies only a
   `content_type`, never a path component.
4. Image sanitisation (`app/utils/image_sanitizer.py`) already decoded,
   stripped, and rebuilt every image from raw pixel data, already guarded
   against decompression bombs, and was already tested against real EXIF
   GPS data through the real Flask upload endpoint.
5. `evidence.evidence_object.retention_expires_at` and its supporting
   partial index (`ix_evidence_retention`) existed since the column was
   first added and were never written to by anything. `EvidenceService.
   reap_expired()` existed, was tested, and was never called by any route,
   script, or scheduler.
6. No Firebase Storage Security Rules file existed anywhere in the
   repository.
7. The frontend evidence upload and viewer components (`EvidenceUpload.
   tsx`, `EvidenceViewer.tsx`) were already complete: real multipart
   upload, client-side pre-validation with server-side re-validation,
   upload/failure/retry states, authenticated Blob-fetch retrieval with no
   public or signed URL anywhere, object-URL cleanup on unmount. Eighteen
   existing component tests plus a further integration suite in
   `IncidentsPage.test.tsx` already covered this thoroughly. Nothing here
   needed building.

## 3. The defect: storage never reused the app auth had already built

`FirebaseStorageProvider.__init__(self, *, bucket_name, app=None)` accepts
an `app` parameter, and the module's own docstring states the intent
plainly: "Reuses the Firebase app initialised by `app.security.firebase`
rather than creating a second one — one credential configuration, not
two." The wiring in `_build_storage_provider` never passed it:

```python
# app/__init__.py, before this phase
return FirebaseStorageProvider(bucket_name=app.config["FIREBASE_STORAGE_BUCKET"])
```

`app` therefore defaulted to `None`, and `firebase_admin.storage.
bucket(name, app=None)` resolves against the SDK's **global default app**
— the one app-name slot this codebase deliberately never initialises,
because `FirebaseTokenVerifier.initialize()` always registers a *named*
app (`campusshield-<project id>`) specifically so two Flask components
never fight over the default. In any real deployment with
`STORAGE_PROVIDER=firebase`, the first storage operation — the first
`put()`, on the first evidence upload — would have raised `ValueError: The
default Firebase app does not exist. Make sure to initialize the SDK by
calling initialize_app().`

This had never surfaced because it had never been exercised: no Firebase
credentials have existed in any environment this project has run in,
including this one, so `STORAGE_PROVIDER=firebase` has never actually been
started end to end before this phase's own testing.

**The fix.** `FirebaseTokenVerifier` gained a public `.app` property.
`_build_storage_provider` now does:

```python
verifier = build_verifier(app.config)
return FirebaseStorageProvider(
    bucket_name=app.config["FIREBASE_STORAGE_BUCKET"], app=verifier.app
)
```

This is correct for both real configurations, and both are verified with a
real (uncredentialed but genuinely-executing) `firebase_admin` SDK, not a
mock of it:

- **`AUTH_PROVIDER=firebase` and `STORAGE_PROVIDER=firebase`.** Auth
  already initialised the named app; `.app` resolves the *identical*
  object via `firebase_admin.get_app(name)` — a process-global, name-keyed
  lookup — rather than creating a second one. Proven by object identity in
  `tests/test_storage_providers.py::test_storage_reuses_the_same_firebase_
  app_as_auth`.
- **`AUTH_PROVIDER=dev` and `STORAGE_PROVIDER=firebase`.** A legitimate
  combination — real bucket, no Firebase Auth. Storage initialises the
  named app itself, correctly. Proven in `test_storage_initialises_its_
  own_app_when_auth_is_not_firebase`.

`Config._validate_storage_provider` also now requires `FIREBASE_PROJECT_
ID` whenever `STORAGE_PROVIDER=firebase`, independent of `AUTH_PROVIDER` —
previously required only when `AUTH_PROVIDER=firebase`, which left the
dev-auth-plus-real-storage combination able to start and fail on the first
upload instead of at boot, the opposite of this codebase's own stated
philosophy for every other required-configuration check.

## 4. Evidence retention: from a stamp to an actual purge

`retention_days_applied` was stamped correctly at insert since Phase 4B-1.
`retention_expires_at` — the timestamp an actual purge would filter on,
and the column `ix_evidence_retention` was built to index — was left
permanently `NULL`, indistinguishable from "never expires" to any query,
including that index's own predicate.

`evidence.fn_evidence_retention_stamp()` (migration `0006`) now computes
it: `NEW.uploaded_at + make_interval(days => NEW.retention_days_applied)`,
`COALESCE`d against an explicit value exactly as `retention_days_applied`
already was. `NEW.uploaded_at` is available inside the `BEFORE INSERT`
trigger because PostgreSQL fills in column `DEFAULT`s before a `BEFORE
ROW` trigger runs — confirmed directly against a real table
(`tests/test_evidence_upload.py::test_attached_evidence_gets_the_
retention_stamp`), not assumed.

New repository methods, following the exact pattern `reap_expired`/
`expired_pending` already established for pending uploads:
`EvidenceRepository.expired_evidence()` (attached evidence past retention,
not yet purged) and `.mark_purged()` (sets `is_purged`, `purged_at`,
`purge_reason` — the row survives). New service method:
`EvidenceService.purge_expired()`, deleting the storage bytes and marking
each row purged. `purge_reason` is written as the literal `'retention_
expiry'`, one of the three values `ck_evidence_purge_reason` (existing
since Phase 4B-1, never previously exercised by a real write) permits.

Neither `reap_expired()` nor `purge_expired()` was previously invoked
anywhere in the codebase. Both now are, by a new script:

```
backend/scripts/reap_and_purge_evidence.py [--dry-run]
```

**This is explicitly not a scheduler**, matching the directive's own
instruction not to build one. The script is a manual or cron-invoked entry
point; a production deployment needs an external trigger for it (cron, a
platform's own scheduled-job feature). Its own docstring states this, and
so does §15.4 of `BACKEND_ARCHITECTURE.md`.

Full schema-level detail: `DATABASE.md` §31.

## 5. File validation and image sanitisation — re-verified, not rebuilt

Already covered by the existing suite before this phase and re-confirmed
during inspection rather than rewritten: JPEG/PNG/WebP accepted by magic
bytes; a renamed Mach-O binary, HTML, and SVG rejected; a declared
Content-Type never overrides what the bytes actually are; oversized and
malformed images rejected with a specific reason code; a decompression
bomb rejected by pixel-count guard. EXIF GPS, camera make/model, PNG text
chunks, and the embedded EXIF thumbnail are all removed by decode-and-
rebuild-from-raw-pixels, verified by opening the *sanitised* bytes and
finding nothing — not by trusting the sanitiser's own report of success.

Nothing here needed building. What this phase added is coverage of the
`FirebaseStorageProvider` class itself (§7), which the existing suite
could not reach because it only ever ran against
`InMemoryStorageProvider`.

## 6. Evidence access control — re-verified, not rebuilt

Already covered and re-confirmed: a reporter can retrieve their own
evidence; another student cannot (404, not 403 — matching the report
endpoint's own oracle-avoidance); unauthenticated retrieval fails; the
routed authority can retrieve, an unrouted one cannot (a role is not
access); an anonymous reporter can retrieve with their access token; the
submitting account cannot reach anonymous evidence by identity, even its
own upload; an unknown evidence id is 404. This phase adds assertions on
response headers that were set but not previously asserted
(`Content-Disposition: inline`, `Content-Security-Policy: default-src
'none'; sandbox`, absence of `ETag`/`Last-Modified` — no cache validator
that would let a shared cache serve a private response to a second
requester).

## 7. New tests: the provider itself, and the wiring defect

`tests/test_storage_providers.py` (new, 26 tests). Only `firebase_admin.
storage.bucket` is mocked; everything downstream — path building,
exception wrapping, which `app` object actually reaches the SDK — is the
real `FirebaseStorageProvider` class:

- Bucket resolution passes through the exact `app` object the provider was
  constructed with, and only resolves it once (cached).
- `put`/`get`/`delete`/`exists` round-trip correctly against the real
  class; `delete` of a missing object returns `False` without raising;
  `get` of a missing object raises `StorageError`.
- A failed bucket open, and a failed upload, both raise
  `StorageUnavailableError` specifically — a dependency failure must
  surface as 503, not "your image was invalid."
- A storage exception whose message contains a plausible signed-URL-
  bearing string is never logged verbatim — only the exception's type
  name is.
- `is_server_generated()` accepts its own output and rejects nine
  traversal- and malformed-path-shaped strings directly, as a unit, not
  only as an assertion helper inside an upload test.
- `FirebaseStorageProvider` has no `public_url` method — asserted
  structurally.
- The wiring regression itself: `create_app()` with
  `AUTH_PROVIDER=firebase, STORAGE_PROVIDER=firebase` produces a storage
  provider whose `_app` is the *same object* as the auth provider's; with
  `AUTH_PROVIDER=dev, STORAGE_PROVIDER=firebase`, storage still resolves
  a real, correctly-named app rather than `None`. Both against the real
  `firebase_admin` SDK, no mocking — the only reason this is fast and
  network-free is that neither test calls a method that would resolve a
  bucket or verify a token, which is also documented and tested
  separately (`test_firebase_auth.py::test_startup_does_not_probe_
  default_credentials_it_does_not_need`).

`tests/test_evidence_upload.py` (+6): the retention stamp test now also
asserts `retention_expires_at` is set and equals `uploaded_at + 365 days`,
not just that `retention_days_applied` and `metadata_stripped_at` are;
five new tests cover `purge_expired()` — expired evidence is purged and
the row survives marked `is_purged`/`purged_at`/`purge_reason`; a purged
object is immediately unretrievable (404); evidence not yet past
retention is left alone; purging twice only purges once; purging an
object already missing from storage does not raise. The retrieval header
test gained assertions for `Content-Disposition`, `Content-Security-
Policy`, `Content-Length`, and the absence of `ETag`/`Last-Modified`.

`tests/conftest.py`: the `make_image(with_exif=True)` fixture's synthetic
EXIF GPS coordinate (13°9′0″N, 77°32′0″E ≈ 13.15°N, 77.53°E) was
replaced. It sat within roughly 2 km of the real, documented Presidency
University reference coordinate in `CAMPUS_LOCATIONS.md` — close enough,
in the same city, to read as derived from the real campus rather than
arbitrary. This project's standing rule is that no real-looking campus
coordinate is ever written anywhere, including a test fixture whose only
job is proving GPS gets stripped. Replaced with a coordinate in the South
Atlantic, unambiguously synthetic. No test's assertions depend on the
specific value, only on GPS being present before sanitisation and absent
after, so nothing else changed.

**545 backend tests passed.** 514 immediately after the app-reuse fix
alone (§3) — the first full-suite run performed in this phase, before any
test was added — rising to 545 with this phase's own additions: 31 new
tests (26 in `tests/test_storage_providers.py`, 5 in `tests/test_evidence_
upload.py` for `purge_expired()`), plus two existing tests extended with
additional assertions rather than counted as new (the retention stamp
test, the retrieval header test).

## 8. Firebase Storage Security Rules

`storage.rules` (repository root, new) denies every client request
unconditionally:

```
service firebase.storage {
  match /b/{bucket}/o {
    match /{allPaths=**} {
      allow read, write: if false;
    }
  }
}
```

The Admin SDK — the only thing `FirebaseStorageProvider` ever uses —
bypasses Storage Security Rules entirely, so this file is not what makes
evidence private; Flask's own authorisation check
(`can_view_report`) is, and always was. This file is an independent
second guarantee: a bucket left at Firebase's own test-mode default
(`allow read, write: if true`, expiring after 30 days) is publicly
reachable by anyone who learns the bucket name, regardless of anything the
backend does. `firebase.json` (repository root, new) points the Firebase
CLI at it — `firebase deploy --only storage --project <id>`. Neither file
was validated by the real Firebase CLI's own rules linter: attempting to
fetch `firebase-tools` via `npx` in this environment failed on an
unrelated local permissions error before reaching that step. The syntax
follows Firebase's documented minimal deny-all template, but "follows the
documented template" is not the same claim as "the CLI confirmed it
compiles" — that confirmation did not happen here.

## 9. Firebase configuration

No `.env.example` changes were needed — `FIREBASE_PROJECT_ID`,
`FIREBASE_CREDENTIALS_PATH` / `FIREBASE_CREDENTIALS_JSON` /
`GOOGLE_APPLICATION_CREDENTIALS`, `FIREBASE_CHECK_REVOKED`,
`STORAGE_PROVIDER`, and `FIREBASE_STORAGE_BUCKET` were already documented
there, including the correct statement that storage uses "the same Admin
SDK credentials configured above" — a claim that was aspirational before
this phase's fix (§3) and is now actually true. No code path in this
phase logs a credential, a private key, or a storage token; the existing
`test_credential_configuration_errors_do_not_echo_the_secret` test
already covered this for the shared credential-resolution code path that
storage now also goes through.

## 10. What was deliberately not built

A second storage abstraction. A second evidence table. Any change to the
upload flow's ordering (store-before-recording, delete-on-failure —
untouched). Video, audio, or PDF evidence support. A general
notification/scheduler system — `reap_and_purge_evidence.py` is a script,
not a scheduler, and says so. Any frontend change — inspection found
nothing to fix. An update/promote path for evidence past retention. A UI
affordance distinguishing purged from present evidence for a responder
(purged evidence already renders identically to never-existed evidence,
matching the oracle-avoidance pattern used everywhere else in this
codebase).

## 11. What remains genuinely unverified — stated plainly

**Real Firebase Storage — an actual Google-hosted bucket — was not
verified.** No Firebase credentials exist in this environment: `env | grep
-i firebase` and `env | grep -i google_application` both return nothing,
and `backend/.env` carries no Firebase values. This was checked directly
in this phase, not assumed from an earlier one.

What *was* verified, and is not the same claim:

- `FirebaseStorageProvider`'s own logic — path handling, error wrapping,
  idempotent delete, bucket-object caching — against a faked
  `firebase_admin.storage.bucket`, exercising the real class.
- The application-wiring defect and its fix, against a real, executing
  (if uncredentialed) `firebase_admin` SDK — real app initialisation, real
  `get_app`/`initialize_app` calls, real object identity.
- The entire evidence pipeline — upload, sanitisation, EXIF/GPS stripping
  of actually-retrieved bytes, authorization, retention, purge — against
  real PostgreSQL and `InMemoryStorageProvider`.

None of that is a substitute for uploading a real image to a real bucket
and downloading it back through the real application, which is what
"Firebase Storage works" would actually mean. That step needs a real
Firebase project and real credentials, neither of which exist here. If a
future phase or a developer with real credentials wants to perform it, the
concrete steps are: set `STORAGE_PROVIDER=firebase`, `FIREBASE_PROJECT_
ID`, `FIREBASE_STORAGE_BUCKET`, and a real credential in `backend/.env`;
create a scratch account and a scratch report through the real running
API; upload a real JPEG carrying EXIF GPS; confirm the object exists in
the real bucket console; retrieve it through `GET /evidence/<id>`; confirm
the downloaded bytes carry no EXIF or GPS; confirm an unauthorised account
cannot retrieve it; delete the scratch evidence and report; confirm the
Storage object is gone. Use an account and objects clearly labelled as
test data, and remove them afterward — do not leave test evidence in a
real bucket.

---

*End of Phase 4F.*
