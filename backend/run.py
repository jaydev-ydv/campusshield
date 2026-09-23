"""Development entry point.

    python backend/run.py

Loads ``backend/.env`` if present, then serves with Flask's built-in server.
That server is single-threaded and not for production: deploy with gunicorn,
``gunicorn 'app:create_app()' --bind 0.0.0.0:5000``.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = pathlib.Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)


_load_dotenv()

from app import create_app  # noqa: E402
from app.config import ConfigError  # noqa: E402


def main() -> int:
    try:
        app = create_app()
    except ConfigError as exc:
        # Configuration problems are the most common reason a new checkout will
        # not start, and a traceback buries the one line that matters.
        print(f"\nConfiguration error: {exc}\n", file=sys.stderr)
        return 2

    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", "5000"))
    app.run(host=host, port=port, debug=app.config.get("DEBUG", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
