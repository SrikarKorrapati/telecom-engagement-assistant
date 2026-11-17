"""Application boundary checks after removing the HTTP layer."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import workflow
from app.config import Settings
from app.db import initialize_database, make_engine
from app.models import AuditEvent, Customer
from app.service import EngagementService, ServiceError


@pytest.fixture
def service():
    engine = make_engine("sqlite://")
    initialize_database(engine)
    with EngagementService(Settings("sqlite://"), engine) as instance:
        yield instance
    engine.dispose()


@pytest.mark.parametrize("method", [
    "get_customer", "create_run", "list_runs", "get_run", "draft",
    "edit", "critique", "review", "deliver", "simulate",
])
def test_invalid_identity_is_rejected_without_database_access(service, monkeypatch, method):
    def forbidden_session():
        pytest.fail("Invalid identifiers must not reach the database")

    monkeypatch.setattr(service, "sessions", forbidden_session)
    with pytest.raises(ServiceError) as caught:
        getattr(service, method)("not-a-uuid")
    assert caught.value.code == "invalid_input"


@pytest.mark.parametrize("method,payload", [
    ("draft", {"provider": "unsupported"}),
    ("draft", {"skip_validation": True}),
    ("edit", {"subject": "", "body": "Text"}),
    ("review", {"action": "approve", "review_source": "invented"}),
    ("simulate", {"event": "unsupported"}),
])
def test_bad_action_inputs_are_rejected_before_workflow(service, monkeypatch, method, payload):
    def forbidden_session():
        pytest.fail("Invalid action inputs must not reach the database")

    monkeypatch.setattr(service, "sessions", forbidden_session)
    with pytest.raises(ServiceError) as caught:
        getattr(service, method)(str(uuid4()), **payload)
    assert caught.value.code == "invalid_input"


@pytest.mark.parametrize("error_type,code", [
    (OperationalError, "unavailable"), (IntegrityError, "conflict"),
])
def test_failed_mutation_rolls_back_and_hides_database_details(service, monkeypatch, error_type, code):
    def failed_audit(*args, **kwargs):
        # add_customer has already flushed the profile when audit persistence fails.
        raise error_type("SQL-WITH-PRIVATE-DATA", {}, RuntimeError("PRIVATE-CREDENTIAL"))

    with monkeypatch.context() as patch:
        patch.setattr(workflow, "audit", failed_audit)
        with pytest.raises(ServiceError) as caught:
            service.add_customer(service.profile_example())
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)
    assert service.list_customers() == []
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    # The shared service remains usable after the failed transaction.
    customer = service.add_customer(service.profile_example())
    assert service.get_customer(customer["id"])["id"] == customer["id"]


def test_missing_data_and_read_failure_have_safe_distinct_errors(service):
    with pytest.raises(ServiceError) as missing:
        service.get_customer(str(uuid4()))
    assert missing.value.code == "not_found"
    Customer.__table__.drop(service.engine)
    with pytest.raises(ServiceError) as unavailable:
        service.list_customers()
    assert unavailable.value.code == "unavailable"
    assert "SELECT" not in str(unavailable.value)


def test_results_are_json_compatible_detached_and_borrowed_engine_stays_open(service):
    customer = service.add_customer(service.profile_example())
    customer["context"]["synthetic"] = False
    with EngagementService(Settings("sqlite://"), service.engine) as borrowed:
        saved = borrowed.get_customer(customer["id"])
        run = borrowed.create_run(customer["id"])
        assert saved["context"]["synthetic"] is True
        assert isinstance(saved["updated_at"], str)
        assert json.loads(json.dumps(run))["id"] == run["id"]
    # Closing a borrower must not destroy the owner's in-memory database.
    assert service.get_run(run["id"])["id"] == run["id"]


def test_invalid_database_config_is_safe():
    with pytest.raises(ServiceError) as caught:
        EngagementService(Settings("invalid-PRIVATE-CREDENTIAL"))
    assert caught.value.code == "unavailable"
    assert "PRIVATE" not in str(caught.value)
