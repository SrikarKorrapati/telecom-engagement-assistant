"""Idempotent additive prototype migration; preserves existing reviews and seven tables."""

from sqlalchemy import inspect, text


def migrate_review_rubric(engine):
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(7211601)"))
        columns = {column["name"] for column in inspect(connection).get_columns("reviews")}
        if "scores" not in columns:
            kind = "JSONB" if connection.dialect.name == "postgresql" else "JSON"
            connection.execute(text(f"ALTER TABLE reviews ADD COLUMN scores {kind} NOT NULL DEFAULT '{{}}'"))
        if "review_source" not in columns:
            connection.execute(text("ALTER TABLE reviews ADD COLUMN review_source VARCHAR(20) NOT NULL DEFAULT 'unspecified'"))
