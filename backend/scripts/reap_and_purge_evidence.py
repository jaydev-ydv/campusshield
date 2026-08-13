"""Evidence cleanup — abandoned uploads and evidence past its retention period.

    python backend/scripts/reap_and_purge_evidence.py [--dry-run]

Two independent, idempotent cleanup tasks:

* **Reap** — delete pending uploads that were never attached to a report and
  have passed ``PENDING_UPLOAD_TTL_HOURS``. ``EvidenceService.reap_expired()``
  has existed and been tested since Phase 4B-1, but until this script nothing
  in the codebase ever called it.
* **Purge** — delete the storage bytes for evidence attached to a report whose
  retention period has elapsed. ``retention_expires_at`` is computed at insert
  by ``evidence.fn_evidence_retention_stamp()`` (Phase 4F; see migration
  0006). The database row survives, marked ``is_purged`` — a report's record
  of what evidence it once carried is not itself erased, only the bytes and
  the ability to view them. Every read path already filters ``is_purged``.

**This is not a scheduler.** Nothing in this codebase invokes this script on
a timer, and building a general scheduling system is explicitly out of scope
for the phase that added it. A production deployment needs an external
scheduler — cron, a platform's own scheduled-job feature, etc. — running this
on some regular interval. Hourly is reasonable for reaping, given a
6-hour-default pending TTL; daily is reasonable for purging, given a
365-day-default retention. Until that external scheduler is configured,
expired evidence stays exactly where every read path already refuses to serve
it, but its bytes remain in the bucket.

Runs inside a real Flask application context, unlike the other scripts in this
directory: a real ``StorageProvider`` — Firebase or in-memory — is required to
know where to delete bytes from, and that is only ever constructed by
``create_app()``.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows are due without deleting or purging anything.",
    )
    args = parser.parse_args()

    _load_dotenv()

    from app import create_app
    from app.config import ConfigError
    from app.extensions import db
    from app.repositories.evidence_repository import EvidenceRepository
    from app.services.evidence_service import EvidenceService

    try:
        app = create_app()
    except ConfigError as exc:
        print(f"\nConfiguration error: {exc}\n", file=sys.stderr)
        return 2

    with app.app_context():
        repository = EvidenceRepository(db.session)  # type: ignore[arg-type]

        if args.dry_run:
            pending_due = len(repository.expired_pending())
            evidence_due = len(repository.expired_evidence())
            print(f"Would reap {pending_due} abandoned upload(s).")
            print(f"Would purge {evidence_due} evidence object(s) past retention.")
            return 0

        service = EvidenceService(
            evidence=repository,
            storage=app.extensions["storage_provider"],
            max_upload_bytes=app.config["MAX_IMAGE_UPLOAD_BYTES"],
            pending_ttl_hours=app.config["PENDING_UPLOAD_TTL_HOURS"],
        )
        reaped = service.reap_expired()
        purged = service.purge_expired()
        db.session.commit()

        print(f"Reaped {reaped} abandoned upload(s).")
        print(f"Purged {purged} evidence object(s) past retention.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
