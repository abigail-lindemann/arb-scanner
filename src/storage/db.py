"""Single source of truth for the Postgres connection.

Reads DATABASE_URL from the environment (a GitHub Actions Secret in prod).
Never hardcode or commit credentials.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class MissingSecretError(RuntimeError):
    """Raised when a required secret is absent, naming it explicitly."""


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise MissingSecretError(
            f"Required secret '{name}' is not set. Add it as a GitHub Actions "
            f"Secret (Settings -> Secrets and variables -> Actions) or to a local "
            f".env. The agent must not fabricate this value."
        )
    return val


def connect() -> psycopg.Connection:
    """Return a live psycopg connection. Caller is responsible for closing
    (use as a context manager: `with connect() as conn:`)."""
    return psycopg.connect(_require("DATABASE_URL"))


def init_schema(conn: psycopg.Connection | None = None) -> None:
    """Apply schema.sql. Idempotent (all CREATE ... IF NOT EXISTS)."""
    sql = _SCHEMA_PATH.read_text()
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    # Convenience: `python -m src.storage.db` initializes the schema.
    init_schema()
    print("schema applied")
