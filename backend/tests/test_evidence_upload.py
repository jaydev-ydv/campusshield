"""Evidence upload — validation, sanitisation, authorization, anonymity.

**Only the network boundary is mocked.** The storage provider is the in-memory
implementation, so bytes really go in and come out. Image sanitisation runs for
real against Pillow, authorization runs for real against the policy, and every
database constraint and trigger applies. Nothing this system owns is stubbed.

The metadata tests are the ones that matter most: they build images that
genuinely carry EXIF GPS and camera identifiers, put them through the real
sanitiser, and assert the bytes that come out contain neither.
"""

from __future__ import annotations

import io
import uuid
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import text

from app.storage.paths import PREFIX, generate_storage_path, is_server_generated
from app.utils.image_sanitizer import ImageRejectedError, has_metadata, sanitise_image

from .conftest import auth, make_image, upload

# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


def test_valid_jpeg_is_accepted(client, users):
    response = upload(client, users["student"], make_image("JPEG"))
    assert response.status_code == 201
    body = response.get_json()
    assert body["content_type"] == "image/jpeg"
    assert len(body["upload_token"]) == 32


def test_valid_png_is_accepted(client, users):
    response = upload(
        client, users["student"], make_image("PNG"), filename="x.png", content_type="image/png"
    )
    assert response.status_code == 201
    assert response.get_json()["content_type"] == "image/png"


def test_valid_webp_is_accepted(client, users):
    response = upload(
        client, users["student"], make_image("WEBP"), filename="x.webp", content_type="image/webp"
    )
    assert response.status_code == 201
    assert response.get_json()["content_type"] == "image/webp"


def test_renamed_executable_is_rejected(client, users):
    """A Mach-O binary called photo.jpg. The name is a claim; the bytes are not."""
    payload = b"\xcf\xfa\xed\xfe" + b"\x00" * 2048
    response = upload(client, users["student"], payload)
    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["reason"] == "unsupported_format"


def test_html_disguised_as_an_image_is_rejected(client, users):
    response = upload(client, users["student"], b"<html><script>alert(1)</script></html>")
    assert response.status_code == 400


def test_svg_is_rejected(client, users):
    """SVG is a document format that can carry script and fetch remote resources.
    An 'image' that makes network requests is not something to accept here."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    response = upload(client, users["student"], svg, filename="x.svg", content_type="image/svg+xml")
    assert response.status_code == 400


def test_corrupt_image_is_rejected(client, users):
    """Valid JPEG magic bytes, garbage after them."""
    corrupt = b"\xff\xd8\xff" + b"\x00" * 500
    response = upload(client, users["student"], corrupt)
    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["reason"] in {"corrupt", "unsupported_format"}


def test_oversized_image_is_rejected(client, users, app):
    app.config["MAX_IMAGE_UPLOAD_BYTES"] = 1024
    response = upload(client, users["student"], make_image("JPEG", (800, 600)))
    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["reason"] == "too_large"


def test_empty_file_is_rejected(client, users):
    assert upload(client, users["student"], b"").status_code == 400


def test_missing_file_field_is_rejected(client, users):
    response = client.post(
        "/api/v1/evidence",
        data={},
        content_type="multipart/form-data",
        headers=auth(users["student"]),
    )
    assert response.status_code == 400


def test_a_declared_content_type_does_not_override_the_bytes(client, users):
    """A PNG announced as JPEG is stored as a PNG. The bytes decide."""
    response = upload(
        client, users["student"], make_image("PNG"), filename="lie.jpg", content_type="image/jpeg"
    )
    assert response.status_code == 201
    assert response.get_json()["content_type"] == "image/png"


def test_upload_requires_authentication(client):
    response = client.post(
        "/api/v1/evidence",
        data={"file": (io.BytesIO(make_image()), "x.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Metadata sanitisation — run against the real sanitiser
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_the_test_fixture_really_carries_exif():
    """Guards the tests below. If the fixture stopped producing EXIF, every
    sanitisation assertion would pass vacuously."""
    original = make_image("JPEG", with_exif=True)
    assert has_metadata(original)
    with Image.open(io.BytesIO(original)) as image:
        assert image.getexif().get(0x010F) == "TestCameraMake"
        assert dict(image.getexif().get_ifd(0x8825))


@pytest.mark.privacy
def test_exif_gps_is_removed():
    result = sanitise_image(make_image("JPEG", with_exif=True), max_bytes=10_000_000)
    with Image.open(io.BytesIO(result.data)) as image:
        assert not dict(image.getexif().get_ifd(0x8825)), "GPS survived sanitisation"


@pytest.mark.privacy
def test_camera_make_and_model_are_removed():
    result = sanitise_image(make_image("JPEG", with_exif=True), max_bytes=10_000_000)
    with Image.open(io.BytesIO(result.data)) as image:
        exif = image.getexif()
        assert exif.get(0x010F) is None
        assert exif.get(0x0110) is None
        assert exif.get(0x0131) is None


@pytest.mark.privacy
def test_no_metadata_string_survives_in_the_stored_bytes():
    """Stronger than reading fields back: the identifiers must not appear
    anywhere in the file, including in segments Pillow does not parse."""
    result = sanitise_image(make_image("JPEG", with_exif=True), max_bytes=10_000_000)
    for marker in (b"TestCameraMake", b"TestCameraModel", b"CampusShieldTestOS", b"2026:08:10"):
        assert marker not in result.data


@pytest.mark.privacy
def test_sanitised_image_reports_no_metadata():
    result = sanitise_image(make_image("JPEG", with_exif=True), max_bytes=10_000_000)
    assert not has_metadata(result.data)


@pytest.mark.privacy
def test_png_text_chunks_are_removed():
    """PNG carries metadata in tEXt chunks rather than EXIF. A JPEG-shaped
    stripper would miss them entirely."""
    from PIL import PngImagePlugin

    image = Image.new("RGB", (64, 48), (10, 20, 30))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "A Student Name")
    info.add_text("Comment", "taken at the hostel gate")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)

    assert b"A Student Name" in buffer.getvalue()
    result = sanitise_image(buffer.getvalue(), max_bytes=10_000_000)
    assert b"A Student Name" not in result.data
    assert b"hostel gate" not in result.data


@pytest.mark.privacy
def test_sanitised_image_still_opens_and_keeps_its_pixels():
    """Sanitisation must not destroy the evidence itself."""
    original = make_image("JPEG", (200, 150), with_exif=True)
    result = sanitise_image(original, max_bytes=10_000_000)
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.size == (200, 150)
        assert image.format == "JPEG"


def test_sanitiser_records_both_hashes():
    original = make_image("JPEG", with_exif=True)
    result = sanitise_image(original, max_bytes=10_000_000)
    assert len(result.sha256) == 64 and len(result.original_sha256) == 64
    # Different, because the stored bytes are not the uploaded bytes.
    assert result.sha256 != result.original_sha256


def test_sanitiser_rejects_a_decompression_bomb():
    """A small file declaring enormous dimensions."""
    with pytest.raises(ImageRejectedError) as exc:
        sanitise_image(make_image("PNG", (400, 400)), max_bytes=10_000_000, max_pixels=1000)
    assert exc.value.reason == "dimensions"


# ---------------------------------------------------------------------------
# Storage paths — the defect this phase closes
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_storage_paths_are_server_generated_and_opaque(client, users, session):
    upload(client, users["student"], make_image())
    path = session.scalar(text("SELECT storage_path FROM evidence.pending_upload LIMIT 1"))

    assert is_server_generated(path)
    assert path.startswith(f"{PREFIX}/")
    assert str(users["student"].user_id) not in path
    assert users["student"].firebase_uid not in path
    assert users["student"].institutional_email not in path
    assert "photo" not in path


@pytest.mark.privacy
def test_the_original_filename_is_never_stored(client, users, session, report_payload):
    """Not persisted for identified reports either, not only anonymous ones. A
    responder gains nothing from a phone-chosen filename, and it can carry a
    name."""
    token = upload(
        client, users["student"], make_image(), filename="Priya_hostel_incident.jpg"
    ).get_json()["upload_token"]

    assert session.scalar(text("SELECT count(*) FROM evidence.pending_upload")) == 1
    assert not session.scalar(
        text("SELECT count(*) FROM evidence.pending_upload WHERE storage_path LIKE '%Priya%'")
    )

    client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    stored = session.scalar(text("SELECT original_filename FROM evidence.evidence_object"))
    assert stored is None


def test_generated_paths_are_unique():
    paths = {generate_storage_path("image/jpeg") for _ in range(200)}
    assert len(paths) == 200


@pytest.mark.privacy
@pytest.mark.parametrize(
    "hostile",
    [
        "../../another-report/image.jpg",
        "evidence/00000000000000000000000000000000.jpg",
        "/etc/passwd",
        "gs://other-bucket/secret.jpg",
        "evidence/../../../secrets",
    ],
)
def test_a_client_supplied_storage_path_is_refused(client, users, report_payload, hostile):
    """The path-confusion attack. The field no longer exists in the contract, and
    naming it is rejected rather than ignored so a probe leaves a trace."""
    response = client.post(
        "/api/v1/reports",
        json=report_payload(storage_path=hostile),
        headers=auth(users["student"]),
    )
    assert response.status_code == 400
    assert "storage_path" in response.get_json()["error"]["details"]["fields"]


@pytest.mark.privacy
def test_the_old_evidence_field_is_refused(client, users, report_payload):
    """The previous contract accepted file metadata directly. Sending it now is
    an error, not something silently dropped."""
    response = client.post(
        "/api/v1/reports",
        json=report_payload(
            evidence=[
                {"storage_path": "evidence/x.jpg", "content_type": "image/jpeg", "byte_size": 1}
            ]
        ),
        headers=auth(users["student"]),
    )
    assert response.status_code == 400
    assert "evidence" in response.get_json()["error"]["details"]["fields"]


@pytest.mark.privacy
def test_the_upload_response_never_reveals_a_path(client, users):
    body = upload(client, users["student"], make_image()).get_json()
    serialised = str(body)
    assert "evidence/" not in serialised
    assert "storage_path" not in serialised


# ---------------------------------------------------------------------------
# Pending uploads — abandoned uploads are not evidence
# ---------------------------------------------------------------------------


def test_upload_creates_no_evidence_object(client, users, session):
    """The invariant: evidence_object rows always belong to a report."""
    upload(client, users["student"], make_image())
    assert session.scalar(text("SELECT count(*) FROM evidence.pending_upload")) == 1
    assert session.scalar(text("SELECT count(*) FROM evidence.evidence_object")) == 0


def test_attaching_moves_pending_to_evidence(client, users, session, report_payload):
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    response = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    assert response.status_code == 201
    assert session.scalar(text("SELECT count(*) FROM evidence.pending_upload")) == 0
    assert session.scalar(text("SELECT count(*) FROM evidence.evidence_object")) == 1


def test_a_token_cannot_be_used_twice(client, users, report_payload):
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    first = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    second = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    assert first.status_code == 201
    assert second.status_code == 400


def test_an_unknown_token_is_refused(client, users, report_payload):
    response = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=["f" * 32]),
        headers=auth(users["student"]),
    )
    assert response.status_code == 400


def test_a_pending_upload_can_be_discarded(client, users, session, storage):
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    assert len(storage.stored_paths) == 1

    response = client.delete(f"/api/v1/evidence/{token}", headers=auth(users["student"]))
    assert response.status_code == 204
    assert session.scalar(text("SELECT count(*) FROM evidence.pending_upload")) == 0
    assert storage.stored_paths == [], "the object must go with the row"


def test_discarding_an_unknown_token_is_404(client, users):
    response = client.delete(f"/api/v1/evidence/{'a' * 32}", headers=auth(users["student"]))
    assert response.status_code == 404


def test_expired_uploads_are_reaped(app, session, users, client, storage):
    """An abandoned upload must not linger on evidence retention."""
    from app.routes.dependencies import evidence_service

    upload(client, users["student"], make_image())
    # created_at moves with it: ck_pending_upload_expiry rightly forbids a row
    # that expired before it existed.
    session.execute(
        text(
            "UPDATE evidence.pending_upload SET created_at = now() - interval '7 hours', "
            "expires_at = now() - interval '1 hour'"
        )
    )
    session.flush()

    assert evidence_service().reap_expired() == 1
    assert session.scalar(text("SELECT count(*) FROM evidence.pending_upload")) == 0
    assert storage.stored_paths == []


def test_an_expired_token_cannot_be_attached(client, users, session, report_payload):
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    session.execute(
        text(
            "UPDATE evidence.pending_upload SET created_at = now() - interval '7 hours', "
            "expires_at = now() - interval '1 hour'"
        )
    )
    session.flush()

    response = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    assert response.status_code == 400


def test_too_many_images_are_refused(client, users, report_payload, app):
    tokens = [
        upload(client, users["student"], make_image()).get_json()["upload_token"]
        for _ in range(app.config["MAX_EVIDENCE_PER_REPORT"] + 1)
    ]
    response = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=tokens),
        headers=auth(users["student"]),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Anonymity
# ---------------------------------------------------------------------------


@pytest.mark.privacy
def test_anonymous_report_can_carry_evidence(client, users, session, report_payload):
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    response = client.post(
        "/api/v1/reports",
        json=report_payload(anonymous=True, evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    assert response.status_code == 201
    assert session.scalar(text("SELECT count(*) FROM evidence.evidence_object")) == 1


@pytest.mark.privacy
def test_anonymous_evidence_creates_no_attribution(client, users, session, report_payload):
    """The guarantee, restated with evidence in the picture."""
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    ref = client.post(
        "/api/v1/reports",
        json=report_payload(anonymous=True, evidence_tokens=[token]),
        headers=auth(users["student"]),
    ).get_json()["public_ref"]

    count = session.scalar(
        text(
            "SELECT count(*) FROM identity.report_attribution a "
            "JOIN core.report r ON r.report_id = a.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    assert count == 0


@pytest.mark.privacy
def test_no_evidence_row_carries_an_identity(client, users, session, report_payload):
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    client.post(
        "/api/v1/reports",
        json=report_payload(anonymous=True, evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    row = session.execute(text("SELECT * FROM evidence.evidence_object")).mappings().one()
    serialised = str(dict(row))

    assert str(users["student"].user_id) not in serialised
    assert users["student"].firebase_uid not in serialised
    assert users["student"].institutional_email not in serialised


@pytest.mark.privacy
def test_pending_upload_has_no_user_column(session):
    """Structural. The upload is claimed with a capability token, so no link
    between a person and an image outlives the request."""
    columns = {
        row[0]
        for row in session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='evidence' AND table_name='pending_upload'"
            )
        )
    }
    for forbidden in ("user_id", "uploaded_by", "firebase_uid", "email", "reporter_id"):
        assert forbidden not in columns


@pytest.mark.privacy
def test_uploading_to_an_anonymous_report_stores_no_gps(client, users, session, report_payload):
    """End to end: an image with GPS goes in, the stored bytes have none."""
    from app.routes.dependencies import evidence_service

    token = upload(client, users["student"], make_image("JPEG", with_exif=True)).get_json()[
        "upload_token"
    ]
    client.post(
        "/api/v1/reports",
        json=report_payload(anonymous=True, evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    evidence_id = session.scalar(text("SELECT evidence_id FROM evidence.evidence_object"))
    stored = evidence_service().read_bytes(evidence_service().get_for_report(evidence_id))

    assert not has_metadata(stored)
    assert b"TestCameraMake" not in stored


# ---------------------------------------------------------------------------
# Retrieval authorization
# ---------------------------------------------------------------------------


def _attach(client, user, report_payload, session, **overrides):
    token = upload(client, user, make_image()).get_json()["upload_token"]
    ref = client.post(
        "/api/v1/reports",
        json=report_payload(evidence_tokens=[token], **overrides),
        headers=auth(user),
    ).get_json()["public_ref"]
    evidence_id = session.scalar(
        text(
            "SELECT e.evidence_id FROM evidence.evidence_object e "
            "JOIN core.report r ON r.report_id = e.report_id WHERE r.public_ref = :ref"
        ),
        {"ref": ref},
    )
    return ref, evidence_id


def test_the_reporter_can_retrieve_their_own_evidence(client, users, session, report_payload):
    _, evidence_id = _attach(client, users["student"], report_payload, session)
    response = client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["student"]))

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Disposition"] == "inline"
    assert response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert response.headers["Content-Length"] == str(len(response.data))
    # No cache stores this response, so no validator that would let a shared
    # cache serve it to a second, unauthorised requester.
    assert "ETag" not in response.headers
    assert "Last-Modified" not in response.headers


@pytest.mark.privacy
def test_another_student_cannot_retrieve_evidence(client, users, session, report_payload):
    _, evidence_id = _attach(client, users["student"], report_payload, session)
    response = client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["other_student"]))
    # 404, matching the report endpoint: a 403 would confirm the id is real.
    assert response.status_code == 404


@pytest.mark.privacy
def test_unauthenticated_retrieval_is_refused(client, users, session, report_payload):
    _, evidence_id = _attach(client, users["student"], report_payload, session)
    assert client.get(f"/api/v1/evidence/{evidence_id}").status_code == 404


@pytest.mark.privacy
def test_the_routed_authority_can_retrieve_evidence(client, users, session, report_payload):
    _, evidence_id = _attach(client, users["student"], report_payload, session)
    assert (
        client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["icc"])).status_code == 200
    )


@pytest.mark.privacy
def test_an_unrouted_authority_cannot_retrieve_evidence(client, users, session, report_payload):
    """A role is not access. Security does not see ICC-routed evidence."""
    _, evidence_id = _attach(client, users["student"], report_payload, session)
    assert (
        client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["security"])).status_code
        == 404
    )


@pytest.mark.privacy
def test_an_anonymous_reporter_can_retrieve_with_their_token(
    client, users, session, report_payload
):
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    created = client.post(
        "/api/v1/reports",
        json=report_payload(anonymous=True, evidence_tokens=[token]),
        headers=auth(users["student"]),
    ).get_json()
    evidence_id = session.scalar(text("SELECT evidence_id FROM evidence.evidence_object"))

    response = client.get(
        f"/api/v1/evidence/{evidence_id}", headers={"X-Report-Token": created["access_token"]}
    )
    assert response.status_code == 200


@pytest.mark.privacy
def test_the_submitting_account_cannot_reach_anonymous_evidence_by_identity(
    client, users, session, report_payload
):
    """Even the account that uploaded it. There is no link to follow."""
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    client.post(
        "/api/v1/reports",
        json=report_payload(anonymous=True, evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    evidence_id = session.scalar(text("SELECT evidence_id FROM evidence.evidence_object"))
    assert (
        client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["student"])).status_code
        == 404
    )


def test_an_unknown_evidence_id_is_404(client, users):
    assert (
        client.get(f"/api/v1/evidence/{uuid.uuid4()}", headers=auth(users["student"])).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Audit and retention
# ---------------------------------------------------------------------------


def test_uploads_and_views_are_audited(client, users, session, report_payload):
    _, evidence_id = _attach(client, users["student"], report_payload, session)
    client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["student"]))

    actions = {
        row[0]
        for row in session.execute(
            text("SELECT action FROM audit.access_log WHERE object_type = 'evidence'")
        )
    }
    assert "evidence.upload" in actions
    assert "evidence.view" in actions


def test_a_denied_retrieval_is_audited(client, users, session, report_payload):
    _, evidence_id = _attach(client, users["student"], report_payload, session)
    client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["other_student"]))

    denied = session.scalar(
        text(
            "SELECT count(*) FROM audit.access_log "
            "WHERE action = 'evidence.view' AND outcome = 'denied'"
        )
    )
    assert denied == 1


@pytest.mark.privacy
def test_audit_detail_carries_no_path_or_bytes(client, users, session, report_payload):
    _attach(client, users["student"], report_payload, session)
    details = (
        session.execute(
            text("SELECT detail::text FROM audit.access_log WHERE object_type = 'evidence'")
        )
        .scalars()
        .all()
    )

    for detail in details:
        assert "evidence/" not in (detail or "")
        assert "storage_path" not in (detail or "")


def test_attached_evidence_gets_the_retention_stamp(client, users, session, report_payload):
    _attach(client, users["student"], report_payload, session)
    row = session.execute(
        text(
            "SELECT retention_policy_key, retention_days_applied, metadata_stripped_at, "
            "retention_expires_at, uploaded_at, is_purged "
            "FROM evidence.evidence_object"
        )
    ).one()
    assert row[0] == "evidence_retention_days"
    assert row[1] == 365
    assert row[2] is not None
    assert row[3] is not None, "retention_expires_at must be computed at insert, not left NULL"
    assert row[3] - row[4] == timedelta(days=365)
    assert row[5] is False


@pytest.mark.privacy
def test_pending_uploads_do_not_get_evidence_retention(client, users, session):
    """An abandoned upload must expire in hours, not inherit 365 days."""
    upload(client, users["student"], make_image())
    columns = {
        row[0]
        for row in session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='evidence' AND table_name='pending_upload'"
            )
        )
    }
    assert "retention_days_applied" not in columns
    assert "retention_policy_key" not in columns

    ttl = session.execute(
        text("SELECT (expires_at - created_at) < interval '24 hours' FROM evidence.pending_upload")
    ).scalar_one()
    assert ttl is True


# ---------------------------------------------------------------------------
# Purging evidence past retention
# ---------------------------------------------------------------------------


def _expire_all_evidence(session) -> None:
    session.execute(
        text("UPDATE evidence.evidence_object SET retention_expires_at = now() - interval '1 hour'")
    )
    session.flush()


def test_expired_evidence_is_purged(client, users, session, report_payload, storage):
    """Attached evidence past its retention period must have its bytes deleted,
    while the row survives marked purged — read paths already filter it out."""
    from app.routes.dependencies import evidence_service

    _, evidence_id = _attach(client, users["student"], report_payload, session)
    assert len(storage.stored_paths) == 1

    _expire_all_evidence(session)

    assert evidence_service().purge_expired() == 1
    assert storage.stored_paths == [], "the bytes must be deleted"

    row = session.execute(
        text(
            "SELECT is_purged, purged_at, purge_reason FROM evidence.evidence_object "
            "WHERE evidence_id = :id"
        ),
        {"id": evidence_id},
    ).one()
    assert row[0] is True
    assert row[1] is not None
    assert row[2] == "retention_expiry"


@pytest.mark.privacy
def test_purged_evidence_is_no_longer_retrievable(client, users, session, report_payload):
    """A purge must take effect immediately for reads, the same as any other
    evidence deletion — no residual window where a purged object still serves."""
    from app.routes.dependencies import evidence_service

    _, evidence_id = _attach(client, users["student"], report_payload, session)
    _expire_all_evidence(session)
    evidence_service().purge_expired()

    response = client.get(f"/api/v1/evidence/{evidence_id}", headers=auth(users["student"]))
    assert response.status_code == 404


def test_evidence_not_yet_past_retention_is_not_purged(
    client, users, session, report_payload, storage
):
    from app.routes.dependencies import evidence_service

    _attach(client, users["student"], report_payload, session)
    assert evidence_service().purge_expired() == 0
    assert len(storage.stored_paths) == 1


def test_purging_twice_only_purges_once(client, users, session, report_payload):
    """A scheduled job calling this repeatedly must not re-purge, or re-audit,
    the same object."""
    from app.routes.dependencies import evidence_service

    _attach(client, users["student"], report_payload, session)
    _expire_all_evidence(session)

    assert evidence_service().purge_expired() == 1
    assert evidence_service().purge_expired() == 0


def test_purge_of_an_already_missing_storage_object_does_not_raise(
    client, users, session, report_payload, storage
):
    """The mirror of the reaper's own idempotency guarantee: if the bytes are
    already gone from storage — say, a previous purge ran but the transaction
    that recorded it did not commit — running purge again must not raise."""
    from app.routes.dependencies import evidence_service

    _attach(client, users["student"], report_payload, session)
    _expire_all_evidence(session)
    storage.clear()

    assert evidence_service().purge_expired() == 1


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_a_storage_failure_creates_no_database_row(client, users, session, app, monkeypatch):
    """The system must never record evidence for an object that was not stored."""
    from app.storage.provider import StorageUnavailableError

    def explode(*_args, **_kwargs):
        raise StorageUnavailableError("bucket unreachable")

    monkeypatch.setattr(app.extensions["storage_provider"], "put", explode)

    response = upload(client, users["student"], make_image())
    assert response.status_code == 503
    assert session.scalar(text("SELECT count(*) FROM evidence.pending_upload")) == 0


def test_a_database_failure_removes_the_stored_object(app, session, users, monkeypatch):
    """The mirror case: bytes stored, row not written, so the bytes are removed
    rather than orphaned where no policy governs them."""
    from app.repositories.evidence_repository import EvidenceRepository
    from app.services.evidence_service import EvidenceService

    storage = app.extensions["storage_provider"]
    storage.clear()

    repository = EvidenceRepository(session)
    monkeypatch.setattr(
        repository, "add_pending", lambda **_: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    service = EvidenceService(
        evidence=repository, storage=storage, max_upload_bytes=10_000_000, pending_ttl_hours=6
    )

    with pytest.raises(RuntimeError):
        service.upload(make_image())
    assert storage.stored_paths == [], "the orphaned object must be removed"


def test_a_failed_report_submission_leaves_no_evidence(client, users, session, report_payload):
    """Attachment happens inside the report transaction: an invalid report means
    no evidence row either."""
    token = upload(client, users["student"], make_image()).get_json()["upload_token"]
    response = client.post(
        "/api/v1/reports",
        json=report_payload(category_id=999999, evidence_tokens=[token]),
        headers=auth(users["student"]),
    )
    assert response.status_code == 400
    assert session.scalar(text("SELECT count(*) FROM evidence.evidence_object")) == 0
    # The upload survives, so the student can fix the report and resubmit.
    assert session.scalar(text("SELECT count(*) FROM evidence.pending_upload")) == 1


def test_reports_still_work_without_evidence(client, users, report_payload):
    """Evidence is optional and must stay optional."""
    assert (
        client.post(
            "/api/v1/reports", json=report_payload(), headers=auth(users["student"])
        ).status_code
        == 201
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_evidence_config_is_served(client, users, app):
    body = client.get("/api/v1/evidence/config", headers=auth(users["student"])).get_json()
    assert body["max_bytes"] == app.config["MAX_IMAGE_UPLOAD_BYTES"]
    assert set(body["accepted_types"]) == {"image/jpeg", "image/png", "image/webp"}


def test_production_refuses_in_memory_storage():
    from app.config import ConfigError, ProductionConfig

    cfg = ProductionConfig()
    cfg.AUTH_PROVIDER = "firebase"
    cfg.FIREBASE_PROJECT_ID = "demo"
    cfg.CORS_ORIGINS = ["https://campusshield.example.edu"]
    cfg.AUDIT_IP_PEPPER = cfg.REPORT_TOKEN_PEPPER = "x"
    cfg.STORAGE_PROVIDER = "memory"
    with pytest.raises(ConfigError, match=r"memory.*refused in production"):
        cfg.validate()


def test_firebase_storage_requires_a_bucket():
    from app.config import ConfigError, DevelopmentConfig

    cfg = DevelopmentConfig()
    cfg.STORAGE_PROVIDER = "firebase"
    cfg.FIREBASE_STORAGE_BUCKET = None
    with pytest.raises(ConfigError, match="FIREBASE_STORAGE_BUCKET"):
        cfg.validate()
