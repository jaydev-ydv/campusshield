"""End-to-end evidence pipeline against a REAL Firebase Storage bucket.

    python backend/scripts/e2e_real_firebase_storage.py

This is the test every earlier phase's own documentation (`PHASE_4F_
FIREBASE_STORAGE.md` §11) named as the one thing it could not do: no
Firebase credentials existed anywhere in this project until now. This
script is the actual thing that was always planned for the moment they did.

**Only the database is disposable here — the bucket is real.** A local,
throwaway PostgreSQL database is created and dropped exactly like every
other E2E script in this directory (`e2e_campus_map_scenario.py`); nothing
here touches Supabase or any other production database. The bucket,
however, is the real one configured in `backend/.env`
(`FIREBASE_STORAGE_BUCKET`) — this script uploads a real object to it,
downloads it back, and deletes it, verifying deletion actually happened
against the real bucket, not a local stand-in.

**Requires real credentials already configured.** `STORAGE_PROVIDER=
firebase`, `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, and a
reachable `GOOGLE_APPLICATION_CREDENTIALS` (or `FIREBASE_CREDENTIALS_
PATH`/`_JSON`) must already be set in the environment this script runs the
server under. If they are not, every check below fails loudly rather than
silently passing against a fake substitute — this script does not fall
back to the in-memory provider under any circumstance.

Every object this script creates is deleted before it exits, verified by
checking the real bucket afterward, not merely by not raising.
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

DB_NAME = "campusshield_e2e_real_firebase_scratch"
DATABASE_URL = f"postgresql+psycopg://localhost:5432/{DB_NAME}"
PORT = 5058
BASE = f"http://127.0.0.1:{PORT}/api/v1"

PASS, FAIL = "  PASS", "  FAIL"
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    if condition:
        print(f"{PASS}  {label}{(' — ' + detail) if detail else ''}")
    else:
        failures += 1
        print(f"{FAIL}  {label}{(' — ' + detail) if detail else ''}")


def sql(query: str, database: str = DB_NAME) -> str:
    out = subprocess.run(
        ["psql", "-d", database, "-tAX", "-c", query], capture_output=True, text=True
    )
    if out.returncode != 0:
        print(f"psql error: {out.stderr}", file=sys.stderr)
    lines = out.stdout.strip().splitlines()
    return lines[0].strip() if lines else ""


def call(method: str, path: str, *, body=None, headers=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw.decode(errors="replace")}


def upload_photo(user_id: str, *, latitude: float | None, longitude: float | None):
    from tests.test_exif_location import make_photo

    jpeg_bytes = make_photo(latitude=latitude, longitude=longitude)
    boundary = "----RealFirebaseE2E"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="photo.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        + jpeg_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        f"{BASE}/evidence",
        method="POST",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Dev-User": user_id,
        },
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def image_has_gps(raw: bytes) -> bool:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        exif = image.getexif()
        if not exif:
            return False
        return bool(exif.get_ifd(0x8825))


def real_storage_provider():
    """The actual FirebaseStorageProvider this run's server is using, built
    the identical way `app._build_storage_provider` does — used only to ask
    the real bucket "does this object exist" for cleanup verification, never
    to bypass the application's own upload/delete code paths."""
    from app.security.firebase import build_verifier
    from app.storage.firebase_provider import FirebaseStorageProvider

    verifier = build_verifier(
        {
            "FIREBASE_PROJECT_ID": os.environ["FIREBASE_PROJECT_ID"],
            "FIREBASE_CREDENTIALS_PATH": os.environ.get("FIREBASE_CREDENTIALS_PATH"),
            "FIREBASE_CREDENTIALS_JSON": os.environ.get("FIREBASE_CREDENTIALS_JSON"),
        }
    )
    return FirebaseStorageProvider(
        bucket_name=os.environ["FIREBASE_STORAGE_BUCKET"], app=verifier.app
    )


def create_scratch_database() -> None:
    subprocess.run(["dropdb", "--if-exists", DB_NAME], check=False, capture_output=True)
    created = subprocess.run(["createdb", DB_NAME], capture_output=True, text=True)
    if created.returncode != 0:
        print(f"cannot create scratch database: {created.stderr}", file=sys.stderr)
        raise SystemExit(2)
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
        capture_output=True,
        text=True,
    )
    if migrated.returncode != 0:
        print(f"alembic upgrade failed:\n{migrated.stdout}\n{migrated.stderr}", file=sys.stderr)
        raise SystemExit(2)


def drop_scratch_database() -> None:
    subprocess.run(["dropdb", "--if-exists", DB_NAME], check=False, capture_output=True)


def seed_fixtures() -> dict:
    zone_id = sql(
        "INSERT INTO core.campus_zone (code, name) "
        "VALUES ('RFB-ZONE', 'Real Firebase E2E Zone') RETURNING zone_id"
    )
    location_id = sql(
        "INSERT INTO core.campus_location "
        "(code, name, location_type, zone_id, latitude, longitude, coordinate_status, "
        " coordinate_source, coordinate_captured_at, is_indoor, dispatch_note, is_active) "
        "VALUES ('RFB-LIBRARY', 'Real Firebase E2E Library (SYNTHETIC)', 'library', "
        f"{zone_id}, 0.0, 0.0, 'verified', "
        "'SYNTHETIC E2E FIXTURE — not a real place', now(), true, "
        "'Enter via the scratch-test door', true) "
        "RETURNING location_id"
    )
    category_id = sql(
        "INSERT INTO core.report_category "
        "(code, label, kind, routes_to_role, base_severity, requires_confidentiality, "
        " emergency_eligible) "
        "VALUES ('RFB_CAT', 'Real Firebase E2E category', 'incident', 'icc', 1, false, false) "
        "RETURNING category_id"
    )
    accounts = {}
    for key, role, uid in [
        ("student", "student", f"rfb-student-{uuid.uuid4().hex[:8]}"),
        ("other_student", "student", f"rfb-other-student-{uuid.uuid4().hex[:8]}"),
        ("icc", "icc", f"rfb-icc-{uuid.uuid4().hex[:8]}"),
    ]:
        user_id = sql(
            "INSERT INTO identity.app_user (firebase_uid, role, institutional_email) "
            f"VALUES ('{uid}', '{role}', '{uid}@e2e-scratch.invalid') RETURNING user_id"
        )
        accounts[key] = user_id
    return {"location_id": location_id, "category_id": category_id, **accounts}


def start_server() -> subprocess.Popen:
    required = ["STORAGE_PROVIDER", "FIREBASE_PROJECT_ID", "FIREBASE_STORAGE_BUCKET"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"Missing required environment variables: {missing}", file=sys.stderr)
        print("This script refuses to run against anything but real Firebase Storage.")
        raise SystemExit(2)
    if os.environ["STORAGE_PROVIDER"] != "firebase":
        print(
            f"STORAGE_PROVIDER={os.environ['STORAGE_PROVIDER']!r}, not 'firebase'. Refusing to "
            "run — this script exists specifically to test the real provider.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    env = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,
        "FLASK_RUN_PORT": str(PORT),
        "FLASK_RUN_HOST": "127.0.0.1",
        "AUTH_PROVIDER": "dev",
        "FLASK_ENV": "development",
        "AUDIT_IP_PEPPER": "e2e-real-firebase-pepper-0000000000000000",
        "REPORT_TOKEN_PEPPER": "e2e-real-firebase-pepper-1111111111111111",
        # STORAGE_PROVIDER / FIREBASE_* deliberately NOT overridden — inherited
        # from the real environment, which is the entire point of this script.
    }
    proc = subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=BACKEND_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2) as resp:
                if resp.status == 200:
                    return proc
        except (urllib.error.URLError, OSError):
            pass
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            print(f"server exited early:\n{output}", file=sys.stderr)
            raise SystemExit(2)
        time.sleep(0.3)
    proc.terminate()
    print("server did not become healthy in time", file=sys.stderr)
    raise SystemExit(2)


def stop_server(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    print("Creating local scratch database (never Supabase) and migrating to head...")
    create_scratch_database()
    ids = seed_fixtures()

    print("Starting a real server configured for the REAL Firebase Storage bucket...")
    server = start_server()

    try:
        run_scenario(ids)
    finally:
        print("\nStopping server and dropping the local scratch database...")
        stop_server(server)
        drop_scratch_database()
        remaining = subprocess.run(["psql", "-lqt"], capture_output=True, text=True).stdout
        check("local scratch database no longer exists", DB_NAME not in remaining)

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}\n")
    return 1 if failures else 0


def run_scenario(ids: dict) -> None:
    student = {"X-Dev-User": ids["student"]}
    other_student = {"X-Dev-User": ids["other_student"]}
    icc = {"X-Dev-User": ids["icc"]}
    provider = real_storage_provider()

    def payload(**kw):
        base = {
            "category_id": int(ids["category_id"]),
            "location_id": int(ids["location_id"]),
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "narrative": "A real-Firebase E2E scratch scenario account, long enough to validate.",
        }
        base.update(kw)
        return base

    # 1 — upload a real photo carrying EXIF GPS to the real bucket.
    print("\n1. Upload — real bytes reach the real bucket")
    status, upload_body = upload_photo(ids["student"], latitude=13.5, longitude=-2.5)
    check("photo with EXIF GPS uploaded", status == 201)
    token = upload_body["upload_token"]

    pending_path = sql("SELECT storage_path FROM evidence.pending_upload LIMIT 1")
    check("a storage path was recorded", bool(pending_path))
    check(
        "the object genuinely exists in the real Firebase bucket right now",
        provider.exists(pending_path),
        pending_path,
    )

    # 2 — attach it to a real, identified report.
    print("\n2. Attach — evidence becomes part of a report")
    status, report = call(
        "POST", "/reports", body=payload(evidence_tokens=[token]), headers=student
    )
    check(
        "report created with evidence attached",
        status == 201,
        report.get("public_ref") or json.dumps(report),
    )
    if status != 201:
        raise SystemExit(f"cannot continue: report creation failed with {status}: {report}")
    evidence_id = sql(
        "SELECT e.evidence_id::text FROM evidence.evidence_object e "
        "JOIN core.report r ON r.report_id = e.report_id "
        f"WHERE r.public_ref = '{report['public_ref']}'"
    )
    attached_path = sql(
        f"SELECT storage_path FROM evidence.evidence_object WHERE evidence_id = '{evidence_id}'"
    )
    check(
        "the SAME real object now belongs to evidence_object, not a copy",
        attached_path == pending_path,
    )

    # 3 — retrieve it as the authorised student, from the real bucket, through
    # the real application.
    print("\n3. Retrieve — authorised download from the real bucket")
    req = urllib.request.Request(f"{BASE}/evidence/{evidence_id}", headers=student)
    with urllib.request.urlopen(req) as resp:
        retrieval_status = resp.status
        downloaded = resp.read()
    check("authorised student can download the real object", retrieval_status == 200)
    check(
        "downloaded bytes carry NO GPS EXIF (sanitised before ever reaching Firebase)",
        not image_has_gps(downloaded),
    )
    check("downloaded bytes are non-trivial", len(downloaded) > 100, f"{len(downloaded)} bytes")

    # 4 — an unrelated account cannot retrieve it, even though the object is real.
    print("\n4. Unauthorised access is refused against the real object")
    status, _ = call("GET", f"/evidence/{evidence_id}", headers=other_student)
    check("an unrelated student is refused (404, not 403)", status == 404)
    status, _ = call("GET", f"/evidence/{evidence_id}")
    # This route has no @authenticated decorator by design — an anonymous
    # reporter reaches it via their access token instead — so an
    # unauthenticated caller gets the same 404 an unrelated student does,
    # not 401. Matches the existing, established behaviour asserted by
    # tests/test_evidence_upload.py::test_unauthenticated_retrieval_is_refused.
    check("an unauthenticated caller is refused (404, same oracle-avoidance)", status == 404)

    # 5 — the responder route can reach it too (same policy, different role).
    print("\n5. Routed responder can retrieve the same real object")
    req = urllib.request.Request(f"{BASE}/evidence/{evidence_id}", headers=icc)
    with urllib.request.urlopen(req) as resp:
        check("routed responder can download it", resp.status == 200)

    # 6 — anonymity: a second, anonymous report with real evidence gets no
    # attribution row, even though its bytes are genuinely in the real bucket.
    print("\n6. Anonymous report + real evidence: still no attribution")
    status, anon_upload = upload_photo(ids["student"], latitude=None, longitude=None)
    check("anonymous-report photo uploaded to the real bucket", status == 201)
    anon_token = anon_upload["upload_token"]
    status, anon_report = call(
        "POST",
        "/reports",
        body=payload(anonymous=True, evidence_tokens=[anon_token]),
        headers=student,
    )
    check("anonymous report with real evidence created", status == 201)
    attribution_count = sql(
        "SELECT count(*) FROM identity.report_attribution a "
        "JOIN core.report r ON r.report_id = a.report_id "
        f"WHERE r.public_ref = '{anon_report['public_ref']}'"
    )
    check("no attribution row exists for the anonymous report", attribution_count == "0")
    anon_evidence_row = sql(
        "SELECT e.evidence_id::text, e.storage_path FROM evidence.evidence_object e "
        "JOIN core.report r ON r.report_id = e.report_id "
        f"WHERE r.public_ref = '{anon_report['public_ref']}'"
    )
    _anon_evidence_id, anon_storage_path = anon_evidence_row.split("|")
    check(
        "the real storage path contains no identity — student user_id absent from it",
        str(ids["student"]) not in anon_storage_path,
    )
    check(
        "the real storage path contains no identity — firebase uid substring absent",
        "rfb-student" not in anon_storage_path,
    )

    # 7 — staged-but-never-attached upload: discard() deletes the REAL object.
    print("\n7. Discard — a staged (never-attached) upload deletes the real object")
    status, discard_upload = upload_photo(ids["student"], latitude=None, longitude=None)
    discard_token = discard_upload["upload_token"]
    discard_path = sql(
        "SELECT storage_path FROM evidence.pending_upload ORDER BY created_at DESC LIMIT 1"
    )
    check("staged object exists in the real bucket before discard", provider.exists(discard_path))
    del_req = urllib.request.Request(
        f"{BASE}/evidence/{discard_token}", method="DELETE", headers=student
    )
    try:
        urllib.request.urlopen(del_req)
        delete_status = 204
    except urllib.error.HTTPError as exc:
        delete_status = exc.code
    check("discard request accepted", delete_status == 204)
    check(
        "the real object is GONE from the bucket after discard — verified against Firebase itself, "
        "not merely 'the call did not raise'",
        not provider.exists(discard_path),
    )

    # 8 — attached evidence: purge_expired() deletes the REAL object, row survives.
    print("\n8. Purge — retention expiry deletes the real object, audit row survives")
    sql(
        f"UPDATE evidence.evidence_object SET retention_expires_at = now() - interval '1 hour' "
        f"WHERE evidence_id = '{evidence_id}'"
    )
    from app import create_app
    from app.config import DevelopmentConfig
    from app.extensions import db as _db
    from app.repositories.evidence_repository import EvidenceRepository
    from app.services.evidence_service import EvidenceService

    purge_cfg = DevelopmentConfig()
    purge_cfg.SQLALCHEMY_DATABASE_URI = DATABASE_URL
    purge_cfg.AUTH_PROVIDER = "dev"
    purge_cfg.AUDIT_IP_PEPPER = "x"
    purge_cfg.REPORT_TOKEN_PEPPER = "x"
    purge_cfg.STORAGE_PROVIDER = "firebase"
    purge_cfg.FIREBASE_PROJECT_ID = os.environ["FIREBASE_PROJECT_ID"]
    purge_cfg.FIREBASE_STORAGE_BUCKET = os.environ["FIREBASE_STORAGE_BUCKET"]
    purge_app = create_app(purge_cfg)
    with purge_app.app_context():
        repo = EvidenceRepository(_db.session)  # type: ignore[arg-type]
        service = EvidenceService(
            evidence=repo,
            storage=purge_app.extensions["storage_provider"],
            max_upload_bytes=purge_cfg.MAX_IMAGE_UPLOAD_BYTES,
            pending_ttl_hours=purge_cfg.PENDING_UPLOAD_TTL_HOURS,
        )
        purged_count = service.purge_expired()
        _db.session.commit()
        _db.session.remove()
        # Removing the session returns the connection to the pool, not to the
        # OS — the pool itself must be disposed or dropdb sees a still-open
        # connection and silently fails (drop_scratch_database() swallows
        # the error, so this would otherwise surface only as a mystery
        # leftover database after the run, not as a loud failure here).
        _db.engine.dispose()
    check(
        "purge_expired() purged exactly the one expired object",
        purged_count == 1,
        str(purged_count),
    )
    check(
        "the real evidence object is GONE from the bucket after purge",
        not provider.exists(attached_path),
    )
    is_purged = sql(
        f"SELECT is_purged FROM evidence.evidence_object WHERE evidence_id = '{evidence_id}'"
    )
    check("the database row survives, marked purged (audit trail intact)", is_purged == "t")

    # Final cleanup of the anonymous-report evidence, so nothing real remains.
    print("\n9. Final cleanup of remaining real objects")
    still_exists = provider.exists(anon_storage_path)
    if still_exists:
        deleted = provider.delete(anon_storage_path)
        check("cleanup: anonymous-report real object deleted", deleted)
    check(
        "no real object from this run remains in the bucket",
        not provider.exists(anon_storage_path),
    )


if __name__ == "__main__":
    sys.exit(main())
