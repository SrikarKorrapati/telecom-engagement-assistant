"""Optional transactional policy storage for hosts with ephemeral filesystems."""

from copy import deepcopy
from functools import lru_cache
import json
import os

from sqlalchemy import Column, Integer, MetaData, Table, select, update

from app.config import Settings
from app.db import make_engine
from app.models import json_document
from app.paths import BUNDLED_POLICIES


metadata = MetaData()
configuration = Table("hosted_configuration", metadata,
                      Column("id", Integer, primary_key=True),
                      Column("document", json_document(), nullable=False))


def enabled() -> bool:
    return os.getenv("POLICY_STORAGE") == "database"


@lru_cache(maxsize=1)
def _engine(url):
    return make_engine(url)


def initialize(engine) -> None:
    """Create one configuration row, preserving all previous dashboard edits."""
    from app.policy_admin import validate_configuration

    policy = json.loads((BUNDLED_POLICIES / "policy.json").read_text())
    content = json.loads((BUNDLED_POLICIES / "content.json").read_text())
    validate_configuration(policy, content)
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError("Hosted configuration requires PostgreSQL.")
    with engine.begin() as connection:
        connection.execute(insert(configuration).values(
            id=1, document={"policy": policy, "content": content}
        ).on_conflict_do_nothing(index_elements=[configuration.c.id]))


def read_documents(session=None, *, for_update=False):
    query = select(configuration.c.document).where(configuration.c.id == 1)
    if for_update:
        if session is None:
            raise ValueError("Configuration locking requires a transaction.")
        query = query.with_for_update()
    if session is None:
        with _engine(Settings.from_env().database_url).connect() as connection:
            document = connection.scalar(query)
    else:
        document = session.scalar(query)
    if not isinstance(document, dict) or set(document) != {"policy", "content"}:
        raise ValueError("Hosted configuration has not been initialized.")
    return deepcopy(document["policy"]), deepcopy(document["content"])


def write_documents(session, policy, content):
    session.execute(update(configuration).where(configuration.c.id == 1).values(
        document={"policy": policy, "content": content}))
