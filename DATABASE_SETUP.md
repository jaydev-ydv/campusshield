# CampusShield — Database Setup

How to create the PostgreSQL database and run the migrations.
Schema design lives in [DATABASE.md](DATABASE.md); this file is operational.

**Requirements:** PostgreSQL 15+, Python 3.10+. **No PostgreSQL extensions** — not
PostGIS, not pgvector, not even pgcrypto (`gen_random_uuid()` has been in core
PostgreSQL since 13).

---

## 1. Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-db.txt
cp .env.example .env          # edit DATABASE_URL if your setup differs
createdb campusshield
.venv/bin/alembic upgrade head
```

Verify:

```bash
psql -d campusshield -c "\dt core.*"
```

---

## 2. Install and start PostgreSQL

### macOS (Homebrew) — what this project was tested on

```bash
brew install postgresql@16
brew services start postgresql@16
```

If `psql` is not found afterwards, the formula is keg-only and needs adding to
your `PATH`:

```bash
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc && exec zsh
```

Check it is up:

```bash
pg_isready
```

Expect `accepting connections`. Homebrew makes your macOS username a superuser
with no password, which is why `.env.example` omits credentials.

### Linux (Debian/Ubuntu)

```bash
sudo apt install postgresql-16 && sudo systemctl start postgresql
```

Then create a role for yourself, since on Linux only the `postgres` user exists
initially:

```bash
sudo -u postgres createuser --superuser "$USER" && sudo -u postgres createdb "$USER"
```

### Windows

Install from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/).
The installer starts the service and sets a password for `postgres` — put it in
your `.env`:

```
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/campusshield
```

### Docker (if you would rather not install a server)

```bash
docker run -d --name campusshield-db -e POSTGRES_PASSWORD=devpassword -p 5432:5432 postgres:16
```

```
DATABASE_URL=postgresql+psycopg://postgres:devpassword@localhost:5432/campusshield
```

---

## 3. Create the database

```bash
createdb campusshield
```

The migration creates its own schemas inside it; the database itself must exist
first. To start over, `dropdb campusshield && createdb campusshield`.

---

## 4. Configure the connection

```bash
cp .env.example .env
```

`.env` is git-ignored. The URL must use the **psycopg 3** driver:

```
postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME
```

A bare `postgresql://` also works — `migrations/env.py` rewrites it — but being
explicit avoids surprises. `env.py` resolves the URL in this order: `-x db_url=`
on the command line, then a live Flask app config, then `DATABASE_URL` from the
environment or `.env`.

---

## 5. Run the migrations

```bash
.venv/bin/alembic upgrade head
```

| Task | Command |
|---|---|
| Apply everything | `alembic upgrade head` |
| Current version | `alembic current` |
| History | `alembic history --verbose` |
| Roll back one step | `alembic downgrade -1` |
| Roll back everything | `alembic downgrade base` |
| Preview SQL without connecting | `alembic upgrade head --sql` |
| Override the URL once | `alembic -x db_url=postgresql+psycopg://... upgrade head` |

The whole migration runs in **one transaction**. PostgreSQL has transactional
DDL, so a failure part-way through leaves an empty database rather than a
half-built one — you never have to hand-clean before retrying.

### Roll back completely

```bash
.venv/bin/alembic downgrade base
```

Drops all 8 schemas with `CASCADE` and then the 24 enum types, leaving the
database empty. **This destroys all data.** Verified to leave zero residue.

---

## 6. Verify the install

```bash
./scripts/test_migration.sh
```

Drops and recreates a test database, applies the migration, checks every object
count, runs all structural guarantee checks, downgrades, confirms zero residue,
re-applies, and cleans up. It refuses to run against a database whose name does
not contain `test`.

To check guarantees against a database you want to keep:

```bash
psql -d campusshield -f scripts/verify_schema_guarantees.sql
```

That script runs inside a transaction it rolls back, so it leaves no data. It
exercises 44 checks including: an anonymous report cannot acquire an attribution
row; `submission_mode` cannot be changed after insert; an anonymous report cannot
be marked contactable; a contact attempt cannot be recorded against a
non-contactable reporter; `reporter_relationship` is rejected from risk factors;
audit tables reject UPDATE and DELETE; k=3 suppression omits low-count locations
entirely; and a difference-in-differences result cannot be stored without its
control figures.

---

## 7. Optional post-migration scripts

Neither runs automatically.

### Privilege separation (recommended)

```bash
# CHANGE THE PASSWORDS IN THE FILE FIRST — they are placeholders.
psql -d campusshield -f sql/roles_and_grants.sql
```

Creates four database roles and the grants that make the privacy separation
real. Roles are cluster-level objects rather than database objects, which is why
this is not in the migration: `CREATE ROLE` would fail on the second environment
that already has them, and rollback semantics would be ambiguous.

The script ends with checks that print the property worth showing an examiner —
`cs_analytics` can read report metadata, hotspots, and impact measurements, and
**cannot** read `core.report_narrative` or reach the `identity` schema at all.
That is a `GRANT` table, not a promise.

### Report taxonomy (review before applying)

```bash
psql -d campusshield -f sql/seed_report_categories.sql
```

16 proposed categories. Not applied automatically because `routes_to_role`
decides which authority sees which report, which is an institutional judgement.
Read the header notes, adjust, then apply.

**No campus locations or coordinates are seeded anywhere.** See
[CAMPUS_LOCATIONS.md](CAMPUS_LOCATIONS.md): zero coordinates are verified, and
none will be invented. `core.campus_location` starts empty.

---

## 8. Adding rows to the location vocabulary

`core.campus_location` records coordinate verification status explicitly, so the
survey backlog lives in the table rather than in a document.

**Loading more than one or two rows — use the importer (Phase 5).**
`backend/scripts/import_campus_locations.py` reads a CSV, validates every row
against the same constraints described below (coordinate ranges, required
fields, duplicate codes, duplicate coordinates, verified-status sourcing,
active-requires-verified), and reports exactly what it would insert before
writing anything. See `backend/scripts/templates/README.md` for the CSV
format and exactly what real survey data this project still needs. It never
updates an existing row — promoting one from staged to verified stays the
one-row `UPDATE` below, because that is a "someone stood here and confirmed
it" act performed once per place, not a bulk operation.

```bash
python backend/scripts/import_campus_locations.py \
    --zones backend/scripts/templates/zones.csv \
    --locations path/to/real_locations.csv \
    --commit   # omit to preview only — the default
```

**Staging or promoting one row by hand** remains a fine, direct way to work:

```sql
INSERT INTO core.campus_location (code, name, location_type)
VALUES ('LKRC-MAIN', 'Library & Knowledge Resource Centre (LKRC)', 'library');
-- coordinate_status defaults to 'required', is_active defaults to false
```

Promote it once someone has stood there with a phone:

```sql
UPDATE core.campus_location
   SET latitude = 13.xxxxxx, longitude = 77.xxxxxx,
       coordinate_status = 'verified',
       coordinate_source = 'field survey, GPS, main entrance',
       coordinate_captured_at = now(),
       has_lighting = TRUE, has_cctv = FALSE, footfall_band = 'high',
       is_active = TRUE
 WHERE code = 'LKRC-MAIN';
```

Constraints enforce the discipline: `coordinate_status = 'required'` forbids
coordinates outright, `'verified'` demands a source and a capture time, and
`is_active` is impossible without `'verified'`. Nothing un-surveyed can enter the
working vocabulary, and nothing surveyed can claim to be verified without saying
who verified it and when. The importer enforces the same rules before it ever
reaches the database, so a bad CSV fails with a row number instead of an
`IntegrityError`.

Track what is left:

```sql
SELECT coordinate_status, count(*) FROM core.campus_location GROUP BY 1;
```

---

## 9. Flask-Migrate — the change when the backend arrives

Flask-Migrate is Alembic plus a Flask CLI wrapper and this same `migrations/`
directory. Plain Alembic is used now because there are no SQLAlchemy models: the
schema is hand-written DDL with cross-schema foreign keys, partial unique
indexes, PL/pgSQL triggers, and CHECK constraints that SQLAlchemy cannot express.
Autogenerate would propose dropping every one of them, so `target_metadata` is
deliberately `None` and autogenerate is not used.

When the backend lands:

1. `pip install Flask-SQLAlchemy Flask-Migrate`
2. `Migrate(app, db)` — it finds this `migrations/` directory automatically
3. `flask db upgrade` replaces `alembic upgrade head`

No migration file changes. `migrations/env.py` already checks for a live Flask
app config before falling back to `DATABASE_URL`, so both entry points work
during the transition.

**Keep writing migrations by hand.** Even with models present, `--autogenerate`
cannot see triggers, partial indexes, RLS policies, or CHECK constraints, and
will quietly propose removing them. Use it to draft column changes if you like,
then read the diff carefully before applying.

---

## 10. Writing the next migration

```bash
.venv/bin/alembic revision -m "add something"
```

Creates a stub in `migrations/versions/`. Fill in `upgrade()` and `downgrade()`
with `op.execute(...)`, following the pattern in `0001_initial_schema.py`, and
give the revision a sequential id (`0002`, `0003`, …).

Two rules worth keeping:

- **Always write `downgrade()`.** An untested rollback is not a rollback, and the
  moment you need one is the moment you least want to discover it does not work.
- **Never edit an applied migration.** Add a new one. `0001` has run against a
  database somewhere; changing it makes that database's recorded history a lie.

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `no database URL` | No `DATABASE_URL`. `cp .env.example .env`, or pass `-x db_url=...` |
| `ModuleNotFoundError: psycopg2` | The URL uses `postgresql://` and something is bypassing the rewrite. Use `postgresql+psycopg://` explicitly |
| `connection refused` | Server not running. `pg_isready`, then `brew services start postgresql@16` |
| `database "campusshield" does not exist` | `createdb campusshield` — the migration creates schemas, not the database |
| `role "..." does not exist` | Linux: `sudo -u postgres createuser --superuser "$USER"` |
| `permission denied to create schema` | Migration user needs `CREATE` on the database. Run migrations as an owner/superuser, then let `cs_app` connect for normal use |
| `Can't locate revision` | `alembic_version` names a revision not in `versions/`. Check the table against `alembic history` |
| `type "user_role" already exists` | A previous run failed after enum creation. `alembic downgrade base`, or drop and recreate the database |
| Guarantee check fails | A structural guarantee has regressed. Do not work around it — read the failing check in `verify_schema_guarantees.sql` and the constraint it names |

---

## 12. What was tested

Executed against **PostgreSQL 16.14 (Homebrew, aarch64-apple-darwin)** on a real
empty database, not simulated:

| Check | Result |
|---|---|
| Apply to empty database | pass, first attempt |
| Object counts (8 schemas / 34 tables / 1 view / 24 enums / 135 indexes / 19 triggers / 13 policy rows) | pass |
| Extensions required | 0 |
| Foreign-key ordering, circular dependencies | none — tables create in dependency order |
| Enum creation ordering | all 24 created before first use |
| Trigger ordering | all functions created before their triggers; triggers after their tables |
| Constraint conflicts | two found and resolved (see below) |
| `downgrade base` | pass, zero residue |
| upgrade → downgrade → upgrade | pass |
| Offline SQL generation (`--sql`) | pass |
| 44 structural guarantee checks | all pass |
| `sql/roles_and_grants.sql` | pass; `cs_analytics` confirmed unable to read narratives or reach `identity` |

### Conflicts found while building, and how they were resolved

**Audit foreign keys versus append-only.** `audit.access_log.actor_user_id` was
specified as a foreign key to `identity.app_user`. Every available `ON DELETE`
action conflicts with append-only enforcement: `CASCADE` deletes audit rows,
`SET NULL` updates them, and both are blocked by the append-only trigger — which
would in turn make deleting any user impossible. Resolved by giving the audit
tables **no foreign keys at all**, which is standard for audit logs: they must
outlive the rows they describe. Verified by test 15b — the audit trail survives
deletion of the report it describes.

**`case_status_history` append-only versus report cascade.** DATABASE.md §19
requires the table to block `UPDATE` and `DELETE`, while §18 requires reports to
cascade into it. Both cannot hold naively. Resolved by having the trigger block
`UPDATE` unconditionally, and block `DELETE` only when the parent report still
exists — during a cascade the parent row is already gone within the transaction,
so its presence reliably distinguishes a tamper from a cascade. Verified by tests
8b and 15a.

**`report_narrative` redacted-column pairing.** DATABASE.md specifies the same
strict pairing for `narrative_redacted` / `redacted_purged_at` as for the raw
narrative. That is unsatisfiable: the redacted copy is legitimately NULL before
redaction runs, so a strict `IS NULL ⟺ purged` biconditional would reject every
new row. Implemented one-directionally — `redacted_purged_at IS NOT NULL` implies
`narrative_redacted IS NULL` — which is the assertion that actually carries the
privacy meaning.

### Deviations from DATABASE.md, and why

| Deviation | Reason |
|---|---|
| `CITEXT` → `TEXT` + unique index on `lower(institutional_email)` | `citext` is a contrib **extension**; the brief requires none. Case-insensitive uniqueness is preserved exactly. |
| `campus_location.latitude/longitude` nullable | Required to represent coordinate verification status explicitly, as instructed. The original guarantee is preserved by `ck_campus_location_active_requires_verified`: nothing un-surveyed can go live. |
| `intervention.intervention.report_id` added | `intervention_scope` includes `'report'`, but §15's column list has no `report_id`, making that enum value unsatisfiable. Added so the enum and the table agree. |
| `system_policy.value_type` accepts `numeric` | The similarity thresholds (0.72, 0.88) are not integers. |
| 19 triggers, not 14 | The 14 in §19 are all present. Five more enforce rules DATABASE.md states as prose: anonymous reports carry no original filename; control locations must exist and cannot include the treated location; a non-prototype policy origin must cite its source. |
| Audit tables have no foreign keys | See the conflict resolution above. |
