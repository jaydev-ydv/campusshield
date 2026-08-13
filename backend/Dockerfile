# CampusShield backend — production image.
#
# Build from the REPOSITORY ROOT, not backend/, so migrations/ and
# requirements-db.txt are inside the build context:
#
#   docker build -f backend/Dockerfile -t campusshield-backend .
#
# Migrations are deliberately NOT run by this image's own startup command —
# see PRODUCTION_READINESS.md for why (a migration must run once, on
# purpose, never as a side effect of a container restarting or scaling out)
# and for the exact one-off command to run one.
#
# All configuration — DATABASE_URL, AUTH_PROVIDER, FIREBASE_*,
# STORAGE_PROVIDER, the two peppers, RATELIMIT_STORAGE_URI — is read from
# the environment at startup, never baked into this image. See
# backend/.env.example for the full list.

FROM python:3.12-slim

# libpq5 only: psycopg[binary] and Pillow both ship prebuilt wheels for this
# platform, so no compiler toolchain is needed here.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements-db.txt ./
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r requirements-db.txt -r backend/requirements.txt

COPY migrations/ migrations/
COPY alembic.ini ./
# The classification artifact only — not ml/src, ml/configs, or ml/results,
# and not the heavier research dependencies (torch, transformers) those
# pull in. app/ml/artifact.py already treats a missing artifact as a
# supported state (no category suggestion), so this layer is optional in
# spirit; it is included so a deployment gets ML suggestions by default.
COPY ml/artifacts/ ml/artifacts/
COPY backend/app/ backend/app/

# Non-root: nothing this process does needs root — it binds an unprivileged
# port (5000) and writes nothing to disk beyond Python's own temp-file use
# during image decoding, which /tmp already permits any user. Owning /srv
# rather than chowning the whole filesystem keeps the image layer small.
RUN groupadd -r campusshield && useradd -r -g campusshield -d /srv campusshield \
    && chown -R campusshield:campusshield /srv
USER campusshield

WORKDIR /srv/backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 5000

# One worker by default: this is a prototype deployment, sized for a
# demonstration/evaluation audience rather than sustained concurrent
# traffic, and one worker makes the default RATELIMIT_STORAGE_URI=memory://
# backend correct without any extra infrastructure — Phase 4G's own testing
# confirmed the in-memory limiter is exact with one worker and only
# per-worker (not globally exact) with more than one. Scale workers and
# point RATELIMIT_STORAGE_URI at Redis together when real load justifies
# it — see PRODUCTION_READINESS.md and PRODUCTION_DEPLOYMENT.md.
CMD ["gunicorn", "app:create_app()", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "60"]
