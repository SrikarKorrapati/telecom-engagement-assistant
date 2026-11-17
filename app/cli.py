"""Local database and synthetic dataset commands; never sends customer messages."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import initialize_database, make_engine, make_session_factory
from app.models import Customer, EngagementRun, Offer
from app.seed import seed_demo

DEFAULT_DATABASE_URL = "postgresql+psycopg2://telecom@127.0.0.1:55432/telecom_demo"


def aware_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("Timestamp must be ISO 8601 with an explicit timezone") from exc


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_dataset(session, directory: Path) -> dict:
    """Import new identities atomically; preserve all existing customer/offer edits."""
    customers = read_jsonl(directory / "customers.jsonl")
    evaluations = read_jsonl(directory / "evaluation_contexts.jsonl")
    offers = json.loads((directory / "offers.json").read_text(encoding="utf-8"))
    if not isinstance(offers, list):
        raise ValueError("offers.json must be an array")
    histories = {row["customer_id"]: row for row in evaluations}
    customer_ids = {row["id"] for row in customers}
    if len(customer_ids) != len(customers) or len(histories) != len(evaluations):
        raise ValueError("Duplicate customer or evaluation-context identifiers")
    if set(histories) != customer_ids:
        raise ValueError("Every customer must have exactly one matching evaluation context")
    if len({o["id"] for o in offers}) != len(offers) or len({(o["code"], o["version"]) for o in offers}) != len(offers):
        raise ValueError("Duplicate offer identity or code/version")
    existing_offers = session.scalars(select(Offer)).all()
    offer_by_id = {o.id: o for o in existing_offers}
    offer_by_version = {(o.code, o.version): o for o in existing_offers}
    # Detect catalogue conflicts before adding any customer or offer rows.
    for offer in offers:
        UUID(offer["id"])
        if offer["details"].get("synthetic") is not True or type(offer["version"]) is not int or offer["version"] < 1:
            raise ValueError("Only explicitly synthetic, versioned offers may be imported")
        old_id = offer_by_id.get(offer["id"])
        old_version = offer_by_version.get((offer["code"], offer["version"]))
        if old_id and (old_id.code, old_id.version) != (offer["code"], offer["version"]):
            raise ValueError("An existing offer ID has different code/version metadata; import cancelled")
        if old_version and old_version.id != offer["id"]:
            raise ValueError(
                f"Offer {offer['code']} version {offer['version']} already has another identity. "
                "No rows were overwritten. Use a separate empty database for this dataset; do not mix it with --seed-demo."
            )
    for customer in customers:
        UUID(customer["id"])
        if customer["context"].get("synthetic") is not True:
            raise ValueError("Only customers with context.synthetic=true may be imported")
        if not customer["email"].endswith("@example.com"):
            raise ValueError("Dataset contacts must use the reserved example.com domain")
        if type(customer["version"]) is not int or customer["version"] < 1:
            raise ValueError("Customer version must be a positive integer")
        evaluation = histories[customer["id"]]
        aware_time(evaluation["as_of"])
        if not isinstance(evaluation["recent_deliveries"], list):
            raise ValueError("Synthetic fixture delivery history must be an array")
        for event in evaluation["recent_deliveries"]:
            aware_time(event["created_at"])
            if not event["idempotency_key"].startswith(customer["id"] + ":"):
                raise ValueError("Fixture delivery key belongs to a different customer")
    existing_customers = set(session.scalars(select(Customer.id)).all())
    result = {"customers_inserted": 0, "customers_preserved": 0, "offers_inserted": 0, "offers_preserved": 0}
    for offer in offers:
        if offer["id"] in offer_by_id:
            result["offers_preserved"] += 1
        else:
            session.add(Offer(id=offer["id"], code=offer["code"], version=offer["version"],
                              active=offer["active"], details=deepcopy(offer["details"])))
            result["offers_inserted"] += 1
    for customer in customers:
        if customer["id"] in existing_customers:
            result["customers_preserved"] += 1
            continue
        context = deepcopy(customer["context"])
        evaluation = histories[customer["id"]]
        context["fixture_delivery_history"] = deepcopy(evaluation["recent_deliveries"])
        context["fixture_history_synthetic"] = True
        context["fixture_as_of"] = evaluation["as_of"]
        session.add(Customer(id=customer["id"], name=customer["name"], email=customer["email"],
                             scenario=customer["scenario"], context=context, version=customer["version"],
                             updated_at=aware_time(evaluation["as_of"])))
        result["customers_inserted"] += 1
    session.flush()
    return result


def evaluate_all(factory, *, now=None, fixture_time=False, limit=None, skip_existing=False) -> dict:
    from app.workflow import create_run

    with factory() as session:
        query = select(Customer.id).order_by(Customer.id)
        if skip_existing:
            query = query.where(~Customer.id.in_(select(EngagementRun.customer_id)))
        if limit:
            query = query.limit(limit)
        identifiers = session.scalars(query).all()
    strategies = Counter()
    failures = []
    for index, identifier in enumerate(identifiers, start=1):
        with factory() as session:
            try:
                reference = now
                if fixture_time:
                    customer = session.get(Customer, identifier)
                    if customer.context.get("synthetic") is not True or not customer.context.get("fixture_as_of"):
                        raise ValueError("Customer has no synthetic fixture reference time")
                    reference = aware_time(customer.context["fixture_as_of"])
                run = create_run(session, identifier, now=reference)
                session.commit()
                strategies[run.strategy.get("objective", "unknown")] += 1
            except Exception as error:
                session.rollback()
                failures.append({"customer_id": identifier, "error_type": type(error).__name__})
        if index % 250 == 0:
            print(json.dumps({"evaluated": index, "total": len(identifiers), "failed": len(failures)}), flush=True)
    return {"attempted": len(identifiers), "completed": len(identifiers) - len(failures),
            "strategies": dict(strategies), "failures": failures}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Explicit database URL; otherwise use shell/.env configuration.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="Check the configured database without changing data")
    init = commands.add_parser("init-db", help="Create the seven prototype tables")
    init.add_argument("--seed-demo", "--seed", action="store_true", help="Optionally seed 10 examples; do not combine this catalogue with the 5,000-record dataset")
    init.add_argument("--as-of", type=aware_time)
    load = commands.add_parser("load-dataset", help="Idempotently add synthetic customer and offer identities")
    load.add_argument("path", type=Path)
    evaluate = commands.add_parser("evaluate-all", help="Create policy evaluations, committing one transaction per customer")
    reference = evaluate.add_mutually_exclusive_group()
    reference.add_argument("--as-of", type=aware_time)
    reference.add_argument("--fixture-time", action="store_true", help="Replay each synthetic fixture at its saved reference time")
    evaluate.add_argument("--limit", type=int)
    evaluate.add_argument("--skip-existing", action="store_true", help="Skip customers already having a run")
    args = parser.parse_args(argv)
    if args.command == "evaluate-all" and args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    database_url = args.database_url
    if not database_url:
        from dotenv import load_dotenv
        load_dotenv(override=False)
        database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    try:
        if args.command == "health":
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            result = {"status": "ok", "database": engine.dialect.name, "delivery_mode": "mock"}
        elif args.command == "init-db":
            initialize_database(engine)
            inserted = 0
            if args.seed_demo:
                with factory.begin() as session:
                    inserted = seed_demo(session, now=args.as_of)
            result = {"tables_initialized": 7, "demo_customers_inserted": inserted}
        elif args.command == "load-dataset":
            initialize_database(engine)
            with factory.begin() as session:
                result = load_dataset(session, args.path)
        else:
            result = evaluate_all(factory, now=args.as_of, fixture_time=args.fixture_time,
                                  limit=args.limit, skip_existing=args.skip_existing)
        print(json.dumps(result, sort_keys=True))
        return 1 if result.get("failures") else 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError, argparse.ArgumentTypeError) as error:
        print(f"Command failed: {error}", file=sys.stderr)
        return 1
    except SQLAlchemyError as error:
        print(f"Database operation failed ({type(error).__name__}); check database availability and configuration.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
