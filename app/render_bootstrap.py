"""Initialize a hosted demo before opening its HTTP listener. Never call the LLM."""

import os
from pathlib import Path

from sqlalchemy import func, select

from app.access import demo_password
from app.cli import load_dataset
from app.config import Settings
from app.db import initialize_database, make_engine, make_session_factory
from app.models import Customer
from app.paths import initialize_policy_directory
from app.policy import load_policy


def main() -> None:
    settings = Settings.from_env()
    if not demo_password():
        raise ValueError("The hosted launcher requires DEMO_ACCESS_PASSWORD.")
    if not os.getenv("POLICY_DATA_DIR"):
        raise ValueError("The hosted launcher requires POLICY_DATA_DIR on a persistent disk.")
    initialize_policy_directory()
    load_policy()  # Reject corrupt persisted configuration instead of resetting it.
    engine = make_engine(settings.database_url)
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("The hosted demo requires a PostgreSQL DATABASE_URL.")
        initialize_database(engine)
        with make_session_factory(engine).begin() as session:
            count = session.scalar(select(func.count()).select_from(Customer))
            if not count:
                dataset = Path(__file__).resolve().parent.parent / "datasets/telecom_5000"
                result = load_dataset(session, dataset)
                print(f"Imported {result['customers_inserted']} synthetic customers.", flush=True)
            else:
                print(f"Reusing {count} existing customers; saved edits are preserved.", flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
