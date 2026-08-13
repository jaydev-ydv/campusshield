"""End-to-end smoke test against a running server.

    python backend/run.py &
    python backend/scripts/smoke_test.py

Exercises the Phase 1 endpoints over real HTTP and checks the privacy
guarantees at the API surface. Distinct from the pytest suite, which drives the
app in-process: this one proves the thing actually serves.

Reads DATABASE_URL to look up a demo user, location and category. It creates
reports, so point it at a development database only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

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
    out = subprocess.run(["psql", "-d", DB, "-tAX", "-c", query], capture_output=True, text=True)
    return out.stdout.strip()


def call(method: str, path: str, *, body=None, headers=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
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
    user = sql("SELECT user_id FROM identity.app_user WHERE role='student' LIMIT 1")
    loc = sql("SELECT location_id FROM core.campus_location WHERE is_active LIMIT 1")
    cat = sql("SELECT category_id FROM core.report_category WHERE emergency_eligible LIMIT 1")
    concern = sql(
        "SELECT category_id FROM core.report_category WHERE kind='concern' AND is_active LIMIT 1"
    )
    if not user:
        print("no student account: run  python scripts/seed_dev_data.py")
        return 2
    if not cat:
        print(f"no report categories: run  psql -d {DB} -f ../sql/seed_report_categories.sql")
        return 2

    # No active location is the expected state until the field survey is loaded
    # (CAMPUS_LOCATIONS.md: zero verified coordinates). The health, auth, catalog
    # and error checks still run; report submission cannot, because a report
    # anchors to a location and none exists to anchor to.
    can_submit = bool(loc)

    auth = {"X-Dev-User": user}
    when = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    def payload(**kw):
        base = {
            "category_id": int(cat),
            "location_id": int(loc),
            "occurred_at": when,
            "narrative": "A smoke-test account of an incident, long enough to validate.",
        }
        base.update(kw)
        return base

    print("\n1. Health")
    status, body = call("GET", "/health")
    check("liveness is public", status == 200 and body["status"] == "ok")
    status, body = call("GET", "/health/db")
    check(
        "database ready",
        status == 200 and body["ready"],
        f"revision {body.get('migration_revision')}, pg {body.get('server_version')}",
    )

    print("\n2. Authentication")
    status, body = call("GET", "/locations")
    check("unauthenticated call refused", status == 401, body["error"]["code"])
    status, _ = call(
        "GET", "/locations", headers={"X-Dev-User": "00000000-0000-0000-0000-000000000000"}
    )
    check("unknown user refused", status == 401)

    print("\n3. Catalog")
    status, body = call("GET", "/locations", headers=auth)
    check("locations listed", status == 200, f"{len(body['items'])} active")
    check("no surveillance detail exposed", "has_cctv" not in json.dumps(body))
    status, body = call("GET", "/categories", headers=auth)
    check("categories listed", status == 200, f"{len(body['items'])} active")
    check("no routing detail exposed", "routes_to_role" not in json.dumps(body))

    if not can_submit:
        print("\n4-7. Report submission — SKIPPED")
        print("     No active campus location exists, which is the expected state:")
        print("     zero coordinates have been verified for Presidency University")
        print("     (CAMPUS_LOCATIONS.md), and no synthetic ones will be invented.")
        print("     A report anchors to a location, so submission cannot be")
        print("     exercised until the field survey is loaded.")
        print("\n8. Error shape")
        status, body = call("GET", "/reports/CS-2026-ZZZZZZ", headers=auth)
        err = body["error"]
        check("404 for unknown reference", status == 404)
        check(
            "has code, message, request_id",
            all(k in err for k in ("code", "message", "request_id")),
        )
        print(
            f"\n{'CHECKS PASSED (submission skipped)' if failures == 0 else f'{failures} FAILED'}\n"
        )
        return 1 if failures else 0

    print("\n4. Identified report")
    status, named = call("POST", "/reports", body=payload(), headers=auth)
    check("created", status == 201, named.get("public_ref", ""))
    check("no access token issued", "access_token" not in named)
    check("contactable with consent", named.get("reporter_contactable") is True)
    status, body = call("GET", f"/reports/{named['public_ref']}", headers=auth)
    check("reporter can read it back", status == 200 and body["narrative_available"])
    check("no identity in response", "user_id" not in json.dumps(body))

    print("\n5. Anonymous emergency report")
    status, anon = call(
        "POST",
        "/reports",
        body=payload(anonymous=True, is_emergency=True, is_ongoing=True),
        headers=auth,
    )
    check("created", status == 201, anon.get("public_ref", ""))
    check("emergency flags set", anon["is_emergency"] and anon["is_ongoing"])
    check("NOT contactable", anon["reporter_contactable"] is False)
    check("dispatch has the location", bool(anon["location"]["name"]))
    check("one-time token returned", len(anon.get("access_token", "")) == 32)

    attributions = sql(
        "SELECT count(*) FROM identity.report_attribution a "
        "JOIN core.report r ON r.report_id=a.report_id "
        f"WHERE r.public_ref='{anon['public_ref']}'"
    )
    check("NO attribution row exists", attributions == "0", f"count={attributions}")

    status, _ = call("GET", f"/reports/{anon['public_ref']}", headers=auth)
    check("submitting account cannot reach it by identity", status == 404)

    status, body = call(
        "GET", f"/reports/{anon['public_ref']}", headers={"X-Report-Token": anon["access_token"]}
    )
    check("token holder can read it", status == 200 and body["narrative_available"])

    status, _ = call(
        "GET", f"/reports/{named['public_ref']}", headers={"X-Report-Token": anon["access_token"]}
    )
    check("token does not open another report", status == 404)

    print("\n6. Validation")
    for label, bad in [
        ("invalid category", payload(category_id=32000)),
        ("invalid location", payload(location_id=999999)),
        ("invalid reporter_relationship", payload(reporter_relationship="victim")),
        (
            "emergency on a non-eligible category",
            payload(category_id=int(concern), is_emergency=True),
        )
        if concern
        else ("is_ongoing without emergency", payload(is_ongoing=True)),
        ("unknown field", payload(nonsense=True)),
        ("client-set submission_mode", payload(submission_mode="anonymous")),
        ("client-set user_id", payload(user_id=user)),
    ]:
        status, body = call("POST", "/reports", body=bad, headers=auth)
        check(f"{label} rejected", status == 400, body.get("error", {}).get("code", ""))

    print("\n7. My reports")
    status, body = call("GET", "/reports/mine", headers=auth)
    refs = {item["public_ref"] for item in body["items"]}
    check("identified report listed", named["public_ref"] in refs)
    check("anonymous report absent", anon["public_ref"] not in refs)

    print("\n8. Error shape")
    status, body = call("GET", "/reports/CS-2026-ZZZZZZ", headers=auth)
    err = body["error"]
    check("404 for unknown reference", status == 404)
    check("has code, message, request_id", all(k in err for k in ("code", "message", "request_id")))

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
