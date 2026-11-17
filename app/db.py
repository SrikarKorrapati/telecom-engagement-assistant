"""Explicit database setup. Importing this module never opens a connection."""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base


def make_engine(url: str) -> Engine:
    # Cloud providers omit the driver; use the psycopg2 driver installed by this app.
    # This also avoids a changed default driver in newer SQLAlchemy releases.
    if url.startswith(("postgres://", "postgresql://")):
        url = "postgresql+psycopg2://" + url.split("://", 1)[1]
    parsed = make_url(url)
    options = {"pool_pre_ping": True}
    if parsed.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False}
        if parsed.database in (None, "", ":memory:"):
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def make_session_factory(engine: Engine):
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


def initialize_database(engine: Engine) -> None:
    # Prototype only. Deployments need explicit, versioned migrations.
    Base.metadata.create_all(engine)
    from app.migrations import migrate_review_rubric
    migrate_review_rubric(engine)
