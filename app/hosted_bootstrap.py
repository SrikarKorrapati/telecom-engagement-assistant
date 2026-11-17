"""Initialize the free hosted demo without sending model requests."""

from pathlib import Path

from sqlalchemy import func, select

from app.access import demo_password
from app.cli import load_dataset
from app.config import Settings
from app import configuration_store
from app.db import initialize_database, make_engine, make_session_factory
from app.models import Customer


def initialize() -> None:
    if not demo_password():
        raise ValueError("Hosted access must be configured.")
    if not configuration_store.enabled():
        raise ValueError("Hosted configuration must use database storage.")
    engine = make_engine(Settings.from_env().database_url)
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("The hosted demo requires PostgreSQL.")
        initialize_database(engine)
        configuration_store.initialize(engine)
        with make_session_factory(engine).begin() as session:
            count = session.scalar(select(func.count()).select_from(Customer))
            if not count:
                dataset = Path(__file__).resolve().parent.parent / "datasets/telecom_5000"
                load_dataset(session, dataset)
    finally:
        engine.dispose()
