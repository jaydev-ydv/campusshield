"""End-to-end campus map / location / navigation scenario — scratch database.

    python backend/scripts/e2e_campus_map_scenario.py

Self-contained: creates a throwaway database, migrates it, starts a real
Flask server against it on its own port, drives the *entire* map/location
product story over real HTTP exactly the way a browser would, verifies the
result with direct SQL, then tears down the server and drops the database.
Nothing here touches the shared development database or any other test
database — `campusshield_e2e_map_scratch` is created and dropped by this
script alone, and the name is deliberately unlike any database another tool
in this repository would ever point at.

## Why this exists alongside the pytest suite

`pytest` already runs the sharpest version of most of these individual
checks (`tests/test_incidents.py`, `tests/test_evidence_upload.py`, EXIF
stripping, corroboration, authorization) against a real, disposable
Postgres database of its own. This script is not a replacement for that —
it is a different kind of evidence. It proves the whole story holds
together as *one continuous run against a real server*: a student really
submits two photographed reports through the real upload endpoint, a
responder really opens the real incident queue and reads the real
destination a `NavigationProvider` would target, and the two internal
`report_location_detail` reads pytest does directly are instead reached the
only way a deployed system reaches them — through the API.

## Synthetic, and clearly labelled as such throughout

The one campus location this script creates carries `coordinate_source =
'SYNTHETIC E2E FIXTURE — not a real place'` and sits at `(0.0, 0.0)` — Null
Island, the Gulf of Guinea, the same "obviously not Presidency University"
convention `tests/conftest.py` already uses. Nothing here is a claim about
any real place, and nothing it creates survives the run.
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

DB_NAME = "campusshield_e2e_map_scratch"
DATABASE_URL = f"postgresql+psycopg://localhost:5432/{DB_NAME}"
PORT = 5057
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
    """POST a real JPEG, optionally carrying GPS EXIF, to /evidence.

    Reuses `tests.test_exif_location.make_photo` rather than reimplementing
    EXIF rational encoding — that helper is already the thing pytest's own
    corroboration tests trust to produce a real, readable GPS IFD.
    """
    from tests.test_exif_location import make_photo

    jpeg_bytes = make_photo(latitude=latitude, longitude=longitude)

    boundary = "----E2Eboundary"
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
    """One synthetic verified location, one category, four accounts."""
    zone_id = sql(
        "INSERT INTO core.campus_zone (code, name) "
        "VALUES ('E2E-ZONE', 'E2E Scratch Zone') RETURNING zone_id"
    )
    location_id = sql(
        "INSERT INTO core.campus_location "
        "(code, name, location_type, zone_id, latitude, longitude, coordinate_status, "
        " coordinate_source, coordinate_captured_at, is_indoor, dispatch_note, is_active) "
        "VALUES ('E2E-LIBRARY', 'E2E Scratch Library (SYNTHETIC)', 'library', "
        f"{zone_id}, 0.0, 0.0, 'verified', "
        "'SYNTHETIC E2E FIXTURE — not a real place', now(), true, "
        "'Enter via the scratch-test door', true) "
        "RETURNING location_id"
    )
    # A second location, explicitly is_synthetic=true — Phase 5B's demo/
    # verified distinction. Both locations are equally "test data" in the
    # sense that nothing here is real, but only this one carries the flag
    # that a demo-seeded row in an actual development database would.
    demo_location_id = sql(
        "INSERT INTO core.campus_location "
        "(code, name, location_type, zone_id, latitude, longitude, coordinate_status, "
        " coordinate_source, coordinate_captured_at, is_indoor, dispatch_note, is_active, "
        " is_synthetic) "
        "VALUES ('E2E-DEMO-LIBRARY', 'E2E Demo Library (DEMO FIXTURE)', 'library', "
        f"{zone_id}, 0.002, 0.002, 'verified', "
        "'DEMO FIXTURE: e2e scenario', now(), true, "
        "'Enter via the demo-test door', true, true) "
        "RETURNING location_id"
    )
    category_id = sql(
        "INSERT INTO core.report_category "
        "(code, label, kind, routes_to_role, base_severity, requires_confidentiality, "
        " emergency_eligible) "
        "VALUES ('E2E_HARASS', 'E2E scratch harassment category', 'incident', 'icc', "
        "3, true, true) "
        "RETURNING category_id"
    )

    register_model_if_present()

    accounts = {}
    for key, role, uid in [
        ("student", "student", f"e2e-student-{uuid.uuid4().hex[:8]}"),
        ("other_student", "student", f"e2e-other-student-{uuid.uuid4().hex[:8]}"),
        ("icc", "icc", f"e2e-icc-{uuid.uuid4().hex[:8]}"),
        ("security", "security", f"e2e-security-{uuid.uuid4().hex[:8]}"),
    ]:
        user_id = sql(
            "INSERT INTO identity.app_user (firebase_uid, role, institutional_email) "
            f"VALUES ('{uid}', '{role}', '{uid}@e2e-scratch.invalid') RETURNING user_id"
        )
        accounts[key] = user_id

    return {
        "zone_id": zone_id,
        "location_id": location_id,
        "demo_location_id": demo_location_id,
        "category_id": category_id,
        **accounts,
    }


def register_model_if_present() -> None:
    """Mirror scripts/export_baseline_model.py's own --register step.

    A model artifact being loadable into the running process's memory
    (`MODEL_ARTIFACT_PATH`) and a model being *registered* in
    `ml.model_version` are two separate facts — `MlRepository.
    active_classifier()` reads only the database row, never the file
    directly. A fresh scratch database has no such row until something
    inserts one, so without this, section R below would report "SKIP" on
    every run regardless of whether the artifact exists. All values come
    from the real artifact's own manifest — nothing here is invented.
    """
    artifact_path = PROJECT_ROOT / "ml" / "artifacts" / "tfidf-logreg-1.0.0.joblib"
    if not artifact_path.exists():
        return

    from app.ml.artifact import load_bundle

    bundle = load_bundle(str(artifact_path))
    relative_uri = str(artifact_path.relative_to(PROJECT_ROOT))
    metrics = json.dumps({**bundle.headline_metrics, "data_provenance": bundle.provenance})
    hyperparameters = json.dumps(bundle.hyperparameters)
    trained_at = bundle.trained_at or "now()"
    training_rows = bundle.training_rows if bundle.training_rows is not None else "NULL"

    sql(
        "INSERT INTO ml.model_version "
        "(name, task, family, version, artifact_uri, trained_at, training_rows, "
        " hyperparameters, headline_metrics, is_active) "
        f"VALUES ('{bundle.name}', 'classification', '{bundle.family}', '{bundle.version}', "
        f"'{relative_uri}', '{trained_at}'::timestamptz, {training_rows}, "
        f"'{hyperparameters}'::jsonb, '{metrics}'::jsonb, true)"
    )


def start_server() -> subprocess.Popen:
    env = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,
        "FLASK_RUN_PORT": str(PORT),
        "FLASK_RUN_HOST": "127.0.0.1",
        "AUTH_PROVIDER": "dev",
        "FLASK_ENV": "development",
        "AUDIT_IP_PEPPER": "e2e-scratch-pepper-0000000000000000",
        "REPORT_TOKEN_PEPPER": "e2e-scratch-pepper-1111111111111111",
        "STORAGE_PROVIDER": "memory",
        # Real artifact if one has been exported; absent is a supported state
        # (app/__init__.py::_load_model_bundle degrades gracefully) and
        # section R below reports that honestly rather than skipping silently.
        "MODEL_ARTIFACT_PATH": str(PROJECT_ROOT / "ml" / "artifacts" / "tfidf-logreg-1.0.0.joblib"),
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
            # OSError, not just ConnectionError: TimeoutError is an OSError
            # subclass too, and a slow-to-respond server (e.g. one still
            # loading a real ML artifact at startup) can time out a single
            # connection attempt without the connection itself having failed.
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
    print("Creating scratch database and migrating to head...")
    create_scratch_database()
    ids = seed_fixtures()

    print("Starting a real server against the scratch database...")
    server = start_server()

    try:
        run_scenario(ids)
    finally:
        print("\nStopping server and dropping scratch database...")
        stop_server(server)
        drop_scratch_database()
        remaining = subprocess.run(["psql", "-lqt"], capture_output=True, text=True).stdout
        check("scratch database no longer exists", DB_NAME not in remaining)

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}\n")
    return 1 if failures else 0


def run_scenario(ids: dict) -> None:
    student = {"X-Dev-User": ids["student"]}
    other_student = {"X-Dev-User": ids["other_student"]}
    icc = {"X-Dev-User": ids["icc"]}
    security = {"X-Dev-User": ids["security"]}

    def payload(**kw):
        base = {
            "category_id": int(ids["category_id"]),
            "location_id": int(ids["location_id"]),
            "occurred_at": "2026-08-13T10:00:00+00:00",
            "narrative": "An e2e scratch scenario account of an incident, long enough to validate.",
        }
        base.update(kw)
        return base

    # A/B — fixtures already seeded: one verified synthetic location, one student.
    check("synthetic location seeded and verified", bool(ids["location_id"]))
    check("student account seeded", bool(ids["student"]))

    # C/D/E/F — identified report with a nearby (corroborating) photo.
    print("\nC-F. Identified report with a nearby photo")
    status, upload_body = upload_photo(ids["student"], latitude=0.0003, longitude=0.0003)
    check("photo with nearby GPS uploaded", status == 201)
    near_token = upload_body["upload_token"]

    status, near_report = call(
        "POST", "/reports", body=payload(evidence_tokens=[near_token]), headers=student
    )
    check("report created", status == 201, near_report.get("public_ref", ""))
    check(
        "location is the selected campus location, not a coordinate",
        near_report["location"]["code"] == "E2E-LIBRARY",
    )

    evidence_id = sql(
        "SELECT e.evidence_id::text FROM evidence.evidence_object e "
        "JOIN core.report r ON r.report_id = e.report_id "
        f"WHERE r.public_ref = '{near_report['public_ref']}'"
    )
    status, served = None, None
    req = urllib.request.Request(
        f"{BASE}/evidence/{evidence_id}", headers={"X-Dev-User": ids["student"]}
    )
    with urllib.request.urlopen(req) as resp:
        status = resp.status
        served = resp.read()
    check("evidence served back", status == 200)
    check("served image carries no GPS EXIF", not image_has_gps(served))

    near_resolution = sql(
        "SELECT d.resolution FROM core.report_location_detail d "
        "JOIN core.report r ON r.report_id = d.report_id "
        f"WHERE r.public_ref = '{near_report['public_ref']}'"
    )
    check(
        "nearby photo resolves as corroborated", near_resolution == "corroborated", near_resolution
    )

    # G/H — second report, distant photo.
    print("\nG-H. Second report with a distant photo")
    status, far_upload = upload_photo(ids["student"], latitude=51.5074, longitude=-0.1278)
    check("photo with distant GPS uploaded", status == 201)
    far_token = far_upload["upload_token"]

    status, far_report = call(
        "POST", "/reports", body=payload(evidence_tokens=[far_token]), headers=student
    )
    check("second report created", status == 201, far_report.get("public_ref", ""))

    far_resolution = sql(
        "SELECT d.resolution FROM core.report_location_detail d "
        "JOIN core.report r ON r.report_id = d.report_id "
        f"WHERE r.public_ref = '{far_report['public_ref']}'"
    )
    check("distant photo resolves as conflicting", far_resolution == "conflicting", far_resolution)

    # I — neither photo moved the authoritative location.
    print("\nI. Neither photo changed the authoritative location")
    for ref in (near_report["public_ref"], far_report["public_ref"]):
        loc_code = sql(
            "SELECT l.code FROM core.report r "
            "JOIN core.campus_location l ON l.location_id = r.location_id "
            f"WHERE r.public_ref = '{ref}'"
        )
        check(f"{ref} still anchored at the selected location", loc_code == "E2E-LIBRARY", loc_code)

    # J/K/L/M/N — responder opens the queue and the incident.
    print("\nJ-N. Responder queue and incident detail")
    status, queue = call("GET", "/incidents", headers=icc)
    check("responder can open the queue", status == 200)
    refs_in_queue = {item["public_ref"] for item in queue["items"]}
    check(
        "both reports appear in the queue",
        {near_report["public_ref"], far_report["public_ref"]} <= refs_in_queue,
    )

    status, incident = call("GET", f"/incidents/{near_report['public_ref']}", headers=icc)
    check("incident detail opens", status == 200)
    destination = incident["destination"]
    check("destination is mapped (a real map could draw it)", destination["is_mapped"] is True)
    check("destination is navigable", destination["navigable"] is True)
    check("destination latitude is the surveyed location", destination["latitude"] == 0.0)
    check("destination longitude is the surveyed location", destination["longitude"] == 0.0)
    check(
        "destination is NOT the photo's EXIF coordinate",
        (destination["latitude"], destination["longitude"]) != (0.0003, 0.0003),
    )
    check("dispatch note present for the last hundred metres", bool(destination["dispatch_note"]))
    check("zone name present", destination["zone_name"] == "E2E Scratch Zone")
    check(
        "this location is NOT flagged synthetic (it represents a real, "
        "surveyed-style fixture, distinct from the demo location below)",
        destination["is_synthetic"] is False,
    )
    signal = incident["location_signal"]
    check("incident carries the corroboration state", signal["resolution"] == "corroborated")
    check(
        "responder is not shown the raw photo coordinate",
        "0.0003" not in json.dumps(incident),
    )

    # O — evidence, viewed by the responder.
    print("\nO. Responder views evidence")
    req = urllib.request.Request(
        f"{BASE}/evidence/{evidence_id}", headers={"X-Dev-User": ids["icc"]}
    )
    with urllib.request.urlopen(req) as resp:
        check("responder can open the evidence", resp.status == 200)

    status, _ = call(
        "POST",
        f"/incidents/{near_report['public_ref']}/status",
        body={"target": "triaged"},
        headers=icc,
    )
    check("case can be triaged from the incident view", status == 201)

    # P — the full case lifecycle, one report, start to finish. Not a re-test
    # of Phase 4D in depth — CASE_LIFECYCLE.md's own 60 tests already cover
    # the state machine exhaustively — but proof it composes correctly with
    # everything above: the same report a photo was corroborated against, a
    # responder now assigns, investigates, and resolves, through the real API.
    print("\nP. Full case lifecycle: assign -> under_review -> action_taken -> resolved")
    status, _ = call("POST", f"/incidents/{near_report['public_ref']}/assign", body={}, headers=icc)
    check("responder can assign the case to themselves", status == 201)

    status, _ = call(
        "POST",
        f"/incidents/{near_report['public_ref']}/status",
        body={"target": "under_review", "remark": "Beginning investigation."},
        headers=icc,
    )
    check("case moves to under_review with an active assignment", status == 201)

    status, _ = call(
        "POST",
        f"/incidents/{near_report['public_ref']}/status",
        body={"target": "action_taken", "remark": "Interim measures put in place."},
        headers=icc,
    )
    check("case moves to action_taken", status == 201)

    status, _ = call(
        "POST",
        f"/incidents/{near_report['public_ref']}/status",
        body={
            "target": "resolved",
            "remark": "Investigation concluded.",
            "resolution_reason": "action_taken",
        },
        headers=icc,
    )
    check("case reaches resolved with a terminal reason", status == 201)

    status, student_view = call("GET", f"/reports/{near_report['public_ref']}", headers=student)
    check("the reporting student can read their own report", status == 200)
    check(
        "the student sees the case as resolved, not permanently 'submitted'",
        student_view["status"] == "resolved",
        student_view["status"],
    )
    seen_statuses = {row["status"] for row in student_view["status_history"]}
    check(
        "the student's status history shows real progression, not just one entry",
        {"triaged", "under_review", "action_taken", "resolved"} <= seen_statuses,
        str(sorted(seen_statuses)),
    )
    check(
        "the student is not shown who is assigned to their case",
        "assign" not in json.dumps(student_view).lower(),
    )

    status, mine = call("GET", "/reports/mine", headers=student)
    check("the report appears in the student's own report list", status == 200)
    check(
        "My Reports includes this report",
        near_report["public_ref"] in {item["public_ref"] for item in mine["items"]},
    )

    # Q — a second, emergency report drives the dispatch state machine, kept
    # separate from case status exactly as RESPONDER_ARCHITECTURE.md §2
    # describes: two independent state machines, one report can exercise both.
    print("\nQ. Emergency dispatch lifecycle:")
    print("   pending -> acknowledged -> dispatched -> on_scene -> closed")
    status, emergency_upload = upload_photo(ids["student"], latitude=None, longitude=None)
    check("photo for the emergency report uploaded", status == 201)
    status, emergency_report = call(
        "POST",
        "/reports",
        body=payload(
            is_emergency=True,
            is_ongoing=True,
            evidence_tokens=[emergency_upload["upload_token"]],
        ),
        headers=student,
    )
    check("emergency report created", status == 201, emergency_report.get("public_ref", ""))
    emergency_ref = emergency_report["public_ref"]

    status, dispatch = call("POST", f"/incidents/{emergency_ref}/dispatch", headers=icc)
    check("dispatch raised for an emergency report", status == 201)
    check("dispatch starts pending", dispatch["state"] == "pending", dispatch["state"])

    for target in ("acknowledged", "dispatched", "on_scene", "closed"):
        status, dispatch = call(
            "POST",
            f"/incidents/{emergency_ref}/dispatch/state",
            body={"state": target},
            headers=icc,
        )
        check(f"dispatch advances to {target}", status == 200 and dispatch["state"] == target)

    status, emergency_incident = call("GET", f"/incidents/{emergency_ref}", headers=icc)
    check(
        "the closed dispatch is visible on the incident, case status untouched by it",
        emergency_incident["dispatch"]["state"] == "closed"
        and emergency_incident["status"] == "submitted",
    )

    # R — ML triage runs on submission and never overwrites what the student
    # chose. Requires a real classification model artifact; skipped with a
    # clear reason (not a silent pass) if MODEL_ARTIFACT_PATH did not load —
    # matching the graceful-degradation guarantee itself.
    print("\nR. ML triage suggests, never overwrites")
    model_loaded = sql("SELECT count(*) FROM ml.model_version WHERE is_active")
    if model_loaded == "0":
        print(
            "  SKIP  no active model_version row — MODEL_ARTIFACT_PATH did not load; "
            "this is the documented graceful-degradation state, not a failure"
        )
    else:
        classification_exists = sql(
            "SELECT count(*) FROM ml.report_classification c "
            "JOIN core.report r ON r.report_id = c.report_id "
            f"WHERE r.public_ref = '{near_report['public_ref']}'"
        )
        check(
            "a classification row was produced for the report",
            classification_exists != "0",
        )
        declared_category = sql(
            "SELECT rc.code FROM core.report r "
            "JOIN core.report_category rc ON rc.category_id = r.declared_category_id "
            f"WHERE r.public_ref = '{near_report['public_ref']}'"
        )
        check(
            "the student's declared category was never overwritten by the model",
            declared_category == "E2E_HARASS",
            declared_category,
        )

    # T — navigation targets the authoritative location, never the photo.
    print("\nT. Navigation targets the authoritative campus location")
    check(
        "the destination a NavigationProvider would target is the surveyed "
        "point, not either photo's EXIF coordinate",
        (destination["latitude"], destination["longitude"]) == (0.0, 0.0),
    )

    # U — demo data is fully functional AND unmistakably flagged, and the
    # schema itself refuses an attempt to mislabel a synthetic row (Phase 5B).
    print("\nU. Demo campus data is functional but never masquerades as verified")
    status, demo_report = call(
        "POST",
        "/reports",
        body=payload(location_id=int(ids["demo_location_id"])),
        headers=student,
    )
    check("a report can be filed against a demo location", status == 201)

    status, demo_incident = call("GET", f"/incidents/{demo_report['public_ref']}", headers=icc)
    check("responder can open the demo incident", status == 200)
    demo_destination = demo_incident["destination"]
    check("demo destination is mapped", demo_destination["is_mapped"] is True)
    check("demo destination is navigable", demo_destination["navigable"] is True)
    check(
        "demo destination IS flagged synthetic",
        demo_destination["is_synthetic"] is True,
    )
    check(
        "demo location IS flagged synthetic in the queue summary too",
        demo_incident["location"]["is_synthetic"] is True,
    )

    status, demo_locations = call("GET", "/locations", headers=student)
    demo_item = next(item for item in demo_locations["items"] if item["code"] == "E2E-DEMO-LIBRARY")
    check(
        "the student-facing catalogue also flags the demo location",
        demo_item["is_synthetic"] is True,
    )

    mislabeled = sql(
        "INSERT INTO core.campus_location "
        "(code, name, latitude, longitude, coordinate_status, coordinate_source, "
        " coordinate_captured_at, is_active, is_synthetic) "
        "VALUES ('E2E-MISLABELED', 'Should Be Rejected', 0.5, 0.5, 'verified', "
        "'no demo prefix here', now(), true, true)"
    )
    still_absent = sql("SELECT count(*) FROM core.campus_location WHERE code = 'E2E-MISLABELED'")
    check(
        "the schema itself refuses a synthetic row without the DEMO FIXTURE: "
        "prefix (ck_campus_location_synthetic_is_labelled) — demo data cannot "
        "be mislabeled as verified production data",
        still_absent == "0",
        f"mislabeled insert result={mislabeled!r}",
    )

    # V — unauthorized student access.
    print("\nV. Unauthorized access is refused")
    status, _ = call("GET", "/incidents", headers=student)
    check("a student cannot open the responder queue", status in (403, 404))
    status, _ = call("GET", f"/incidents/{near_report['public_ref']}", headers=student)
    check("a student cannot open incident detail", status in (403, 404))
    status, _ = call("GET", "/incidents", headers=security)
    check(
        "a wrong-role responder sees an empty, not a forbidden, queue "
        "(routing-scoped, matching existing architecture)",
        status == 200,
    )
    if status == 200:
        status, _ = call("GET", f"/incidents/{near_report['public_ref']}", headers=security)
        check("a wrong-role responder cannot open this incident", status == 404)
    status, _ = call("GET", "/incidents")
    check("an unauthenticated caller cannot open the queue", status == 401)

    # W — anonymous report stays unattributed.
    print("\nW. Anonymous report remains unattributed")
    status, anon_report = call(
        "POST", "/reports", body=payload(anonymous=True), headers=other_student
    )
    check("anonymous report created", status == 201)
    attribution_count = sql(
        "SELECT count(*) FROM identity.report_attribution a "
        "JOIN core.report r ON r.report_id = a.report_id "
        f"WHERE r.public_ref = '{anon_report['public_ref']}'"
    )
    check("no attribution row exists for the anonymous report", attribution_count == "0")
    status, _ = call("GET", f"/reports/{anon_report['public_ref']}", headers=other_student)
    check("the submitting account cannot reach it by identity", status == 404)

    # X — audit records exist and carry no sensitive content.
    print("\nX. Audit records")
    audit_rows = sql(
        "SELECT count(*) FROM audit.access_log WHERE action IN "
        "('incident.view', 'evidence.view', 'case.status_change')"
    )
    check("audit rows were written for sensitive actions", int(audit_rows or 0) > 0, audit_rows)
    leak = sql(
        "SELECT count(*) FROM audit.access_log WHERE detail::text ILIKE '%0.0003%' "
        "OR detail::text ILIKE '%scratch scenario account%'"
    )
    check("no coordinate or narrative text leaked into the audit log", leak == "0", leak)


if __name__ == "__main__":
    sys.exit(main())
