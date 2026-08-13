"""End-to-end smoke test for Phase 4E (notifications, profile updates) against
a running server.

    python backend/run.py &
    python backend/scripts/smoke_test_phase4e.py

Distinct from the pytest suite (in-process, real Postgres, every test rolled
back) and from `smoke_test.py` (Phase 1, real socket, real Postgres): this one
proves the same real-socket, real-database guarantee for what this phase
built, without depending on an active campus location — none exist yet
(CAMPUS_LOCATIONS.md), so no report can be submitted through the API, and
this script does not attempt to. It creates exactly two throwaway accounts of
its own and deletes them (and anything they wrote) when it finishes, whether
or not the checks passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("CAMPUSSHIELD_BASE", "http://127.0.0.1:5000/api/v1")
DB = os.environ.get("PGDATABASE", "campusshield")

PASS, FAIL = "  PASS", "  FAIL"
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    if condition:
        print(f"{PASS}  {label}{(' — ' + detail) if detail else ''}")
    else:
        failures += 1
        print(f"{FAIL}  {label}{(' — ' + detail) if detail else ''}")


def sql(query: str) -> str:
    """Run one statement, return its first output line.

    This psql build emits the command tag (`INSERT 0 1`) as a trailing line
    even under `-tAX` for a `RETURNING` statement, so every caller here wants
    line one only — every query in this script returns at most one row.
    """
    out = subprocess.run(["psql", "-d", DB, "-tAX", "-c", query], capture_output=True, text=True)
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
    except urllib.error.URLError as exc:
        print(f"\ncannot reach {BASE}: {exc.reason}\nIs the server running?\n")
        raise SystemExit(2) from exc


def main() -> int:
    icc_uid = f"e2e-smoke-icc-{uuid.uuid4().hex[:8]}"
    student_uid = f"e2e-smoke-student-{uuid.uuid4().hex[:8]}"

    icc_id = sql(
        "INSERT INTO identity.app_user (firebase_uid, role, institutional_email) "
        f"VALUES ('{icc_uid}', 'icc', '{icc_uid}@e2e-smoke.invalid') RETURNING user_id"
    )
    student_id = sql(
        "INSERT INTO identity.app_user (firebase_uid, role, institutional_email) "
        f"VALUES ('{student_uid}', 'student', '{student_uid}@e2e-smoke.invalid') RETURNING user_id"
    )
    check(
        "seeded two throwaway accounts",
        bool(icc_id) and bool(student_id),
        f"icc={icc_id} student={student_id}",
    )

    try:
        icc_auth = {"X-Dev-User": icc_id}
        student_auth = {"X-Dev-User": student_id}

        print("\n1. Identity starts as the server assigned it")
        status, body = call("GET", "/auth/me", headers=icc_auth)
        check("icc account has no display name yet", status == 200 and body["display_name"] is None)
        status, body = call("GET", "/auth/me", headers=student_auth)
        check("student account has no display name", status == 200 and body["display_name"] is None)

        print("\n2. PATCH /auth/me — staff")
        status, body = call(
            "PATCH", "/auth/me", body={"display_name": "E2E Smoke ICC"}, headers=icc_auth
        )
        check("staff can set a display name", status == 200, f"status={status} body={body}")
        check("response reflects the new name", body.get("display_name") == "E2E Smoke ICC")

        status, body = call("GET", "/auth/me", headers=icc_auth)
        check(
            "the name persisted to a fresh request (real commit, not session-local)",
            status == 200 and body["display_name"] == "E2E Smoke ICC",
        )
        stored = sql(f"SELECT display_name FROM identity.app_user WHERE user_id = '{icc_id}'")
        check("the name is in the database via direct SQL", stored == "E2E Smoke ICC", stored)

        print("\n3. PATCH /auth/me — student refused, not silently ignored")
        status, body = call(
            "PATCH", "/auth/me", body={"display_name": "Should Never Land"}, headers=student_auth
        )
        check("student is refused with 403, not 400 or 500", status == 403, f"status={status}")
        stored = sql(f"SELECT display_name FROM identity.app_user WHERE user_id = '{student_id}'")
        check("the student row was not touched", stored == "", f"stored={stored!r}")

        print("\n4. PATCH /auth/me — malformed and unauthenticated requests")
        status, body = call("PATCH", "/auth/me", body={"display_name": ""}, headers=icc_auth)
        check("blank name rejected", status == 400, body.get("error", {}).get("code", ""))
        status, body = call("PATCH", "/auth/me", body={"display_name": "  "}, headers=icc_auth)
        check("whitespace-only name rejected", status == 400, body.get("error", {}).get("code", ""))
        status, body = call("PATCH", "/auth/me", body={"role": "admin"}, headers=icc_auth)
        check("unknown field rejected, not silently dropped", status == 400)
        status, _ = call("PATCH", "/auth/me", body={"display_name": "x"})
        check("unauthenticated PATCH refused", status == 401)

        print("\n5. GET /notifications — a real, empty inbox")
        status, body = call("GET", "/notifications", headers=student_auth)
        check(
            "empty inbox for an account with no notifications",
            status == 200 and body["items"] == [] and body["unread_count"] == 0,
            f"status={status} body={body}",
        )

        print("\n6. POST /notifications/<id>/read — 404s, not 500s")
        status, _ = call("POST", "/notifications/not-a-uuid/read", headers=student_auth)
        check("malformed id is a 400", status == 400)
        status, _ = call(
            "POST",
            f"/notifications/{uuid.uuid4()}/read",
            headers=student_auth,
        )
        check("nonexistent id is a 404, not a 500", status == 404)
        status, _ = call("POST", f"/notifications/{uuid.uuid4()}/read")
        check("unauthenticated mark-read refused", status == 401)

        print("\n7. Endpoint listing advertises the new routes")
        # The index route lives at the application root, not under
        # `API_PREFIX` — `call()` prefixes every other path with `BASE`
        # (which already includes `/api/v1`), so this one is fetched directly.
        root = BASE.rsplit("/api/v1", 1)[0] or "http://127.0.0.1:5000"
        req = urllib.request.Request(f"{root}/", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        endpoints = " ".join(body.get("endpoints", []))
        check("GET /notifications listed", "GET  /api/v1/notifications" in endpoints)
        check(
            "POST /notifications/<id>/read listed",
            "/notifications/<notification_id>/read" in endpoints,
        )
        check("PATCH /auth/me listed", "PATCH /api/v1/auth/me" in endpoints)

        print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}\n")
        return 1 if failures else 0

    finally:
        # `audit.access_log` is deliberately append-only — `trg_access_log_
        # append_only` refuses UPDATE and DELETE outright — so the
        # `account.update_profile` row this run wrote for the throwaway ICC
        # account is not cleaned up here. That is the design working, not a
        # leftover: the audit trail is meant to outlive the account it
        # describes, which is exactly the property being exercised.
        print("Cleaning up throwaway accounts and anything they wrote...")
        ids = f"'{icc_id}', '{student_id}'"
        sql(f"DELETE FROM notify.notification WHERE recipient_user_id IN ({ids})")
        sql(f"DELETE FROM identity.app_user WHERE user_id IN ({ids})")
        remaining = sql(
            f"SELECT count(*) FROM identity.app_user WHERE user_id IN ('{icc_id}', '{student_id}')"
        )
        check("throwaway accounts fully removed", remaining == "0", f"remaining={remaining}")


if __name__ == "__main__":
    sys.exit(main())
