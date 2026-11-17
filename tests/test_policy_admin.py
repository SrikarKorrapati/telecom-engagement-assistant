"""Configuration dashboard saves use isolated files and a disposable database."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import policy_admin, workflow
from app.config import Settings
from app.db import initialize_database, make_engine, make_session_factory
from app.models import AuditEvent, Offer
from app.seed import demo_id, seed_demo
from app.service import EngagementService, ServiceError


@pytest.fixture
def client(tmp_path, monkeypatch):
    for field in ("POLICY_PATH", "CONTENT_PATH"):
        original = getattr(policy_admin, field)
        target = tmp_path / original.name
        target.write_bytes(original.read_bytes())
        monkeypatch.setattr(policy_admin, field, target)
    monkeypatch.setattr(policy_admin, "LOCK_PATH", tmp_path / "policy.lock")
    engine = make_engine("sqlite://")
    initialize_database(engine)
    with make_session_factory(engine)() as session:
        seed_demo(session)
        session.commit()
    service = EngagementService(Settings("sqlite://"), engine)
    yield service
    engine.dispose()


def edit_policy(client):
    current = client.policy_configuration()
    document = deepcopy(current["policy"])
    document["segments"]["loyalty_tenure_months"] = 18
    return current, document


def test_configuration_reads_actual_catalogue_without_writes(client):
    view = client.policy_configuration()
    assert "content" not in view["policy"]
    assert view["offers"]
    assert all(len(view[field]) == 64 for field in ("policy_hash", "content_hash", "catalog_hash"))
    with client.sessions() as session:
        assert not session.scalars(select(AuditEvent).where(AuditEvent.event_type == "configuration_saved")).all()
        saved = session.scalars(select(Offer)).all()
        assert {item["id"] for item in view["offers"]} == {item.id for item in saved}


def test_policy_save_updates_file_hash_and_audit(client):
    before, document = edit_policy(client)
    after = client.save_configuration(section="policy", document=document, expected_hash=before["policy_hash"])
    assert after["policy"] == document == json.loads(policy_admin.POLICY_PATH.read_text())
    assert after["content_hash"] == before["content_hash"]
    assert after["policy_hash"] != before["policy_hash"]
    with client.sessions() as session:
        event = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "configuration_saved")).one()
        assert event.details["previous_hash"] == before["policy_hash"]
        assert event.details["document"] == document
        assert event.run_id is None


def test_policy_save_waits_for_an_in_progress_action(client):
    before, document = edit_policy(client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with policy_admin._lock(shared=True):
            pending = pool.submit(client.save_configuration, section="policy",
                                  document=document, expected_hash=before["policy_hash"])
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.1)
            assert json.loads(policy_admin.POLICY_PATH.read_text()) == before["policy"]
        assert pending.result(timeout=5)["policy"] == document


def test_new_runs_use_saved_configuration_and_old_snapshots_stay_frozen(client, monkeypatch):
    monkeypatch.setattr(workflow, "load_policy", lambda: {
        **json.loads(policy_admin.POLICY_PATH.read_text()),
        "content": json.loads(policy_admin.CONTENT_PATH.read_text()),
    })
    original = client.create_run(demo_id("customer:loyalty"))
    before, document = edit_policy(client)
    client.save_configuration(section="policy", document=document, expected_hash=before["policy_hash"])
    updated = client.create_run(demo_id("customer:loyalty"))
    assert updated["policy_snapshot"]["segments"]["loyalty_tenure_months"] == 18
    assert updated["policy_snapshot"]["version"] == before["policy"]["version"]
    assert client.get_run(original["id"])["policy_snapshot"]["version"] == before["policy"]["version"]
    assert updated["policy_hash"] != original["policy_hash"]


@pytest.mark.parametrize("action", ["draft", "edit", "critique", "approve", "deliver"])
def test_same_name_policy_change_blocks_stale_workflow_actions(client, monkeypatch, action):
    monkeypatch.setattr(workflow, "load_policy", lambda: {
        **json.loads(policy_admin.POLICY_PATH.read_text()),
        "content": json.loads(policy_admin.CONTENT_PATH.read_text()),
    })
    run = client.create_run(demo_id("customer:billing"))
    run = client.draft(run["id"])
    revision = run["revisions"][-1]
    if action == "deliver":
        client.review(revision["id"], action="approve")
    before, changed = edit_policy(client)
    client.save_configuration(section="policy", document=changed, expected_hash=before["policy_hash"])
    monkeypatch.setattr(workflow, "generate_message", lambda *a, **kw: pytest.fail("Stale run reached generator"))
    monkeypatch.setattr(workflow, "critique_message", lambda *a, **kw: pytest.fail("Stale run reached critic"))
    with pytest.raises(ServiceError, match="Policy"):
        if action == "draft":
            client.draft(run["id"])
        elif action == "edit":
            client.edit(revision["id"], subject=revision["subject"], body=revision["body"])
        elif action == "approve":
            client.review(revision["id"], action="approve")
        else:
            getattr(client, action)(revision["id"])
    saved = client.get_run(run["id"])
    assert saved["state"] == "held"
    assert saved["policy_snapshot"]["segments"]["loyalty_tenure_months"] == before["policy"]["segments"]["loyalty_tenure_months"]
    assert saved["deliveries"] == []


def test_content_save_preserves_identity_and_rejects_stale_edits(client):
    before = client.policy_configuration()
    document = deepcopy(before["content"])
    document["signature"] = "Synthetic Customer Care"
    after = client.save_configuration(section="content", document=document, expected_hash=before["content_hash"])
    assert after["policy_hash"] == before["policy_hash"]
    assert after["content"]["version"] == before["content"]["version"]
    document["signature"] = "Another Support Team"
    with pytest.raises(ServiceError, match="changed since") as error:
        client.save_configuration(section="content", document=document, expected_hash=before["content_hash"])
    assert error.value.code == "conflict"
    assert json.loads(policy_admin.CONTENT_PATH.read_text())["signature"] == "Synthetic Customer Care"


def test_changes_keep_one_identity_and_unchanged_save_is_noop(client):
    before, document = edit_policy(client)
    after = client.save_configuration(section="policy", document=document, expected_hash=before["policy_hash"])
    assert after["policy"]["version"] == before["policy"]["version"]
    assert after["policy_hash"] != before["policy_hash"]
    assert client.save_configuration(section="policy", document=document, expected_hash=after["policy_hash"]) == after
    renamed = {**document, "version": "another-name"}
    with pytest.raises(ServiceError, match="current configuration name"):
        client.save_configuration(section="policy", document=renamed, expected_hash=after["policy_hash"])


@pytest.mark.parametrize("path,value", [
    (("extra",), "ignored"), (("example_policy",), 1),
    (("segments", "loyalty_tenure_months"), True),
    (("segments", "decline_current_ratio"), 0.6),
    (("segments", "decline_current_ratio"), "NaN"),
    (("segments", "decline_min_baseline_days"), 31),
    (("outreach", "frequency_days"), 0), (("offer", "channel"), "sms"),
])
def test_invalid_rules_cannot_replace_saved_policy(client, path, value):
    before, document = edit_policy(client)
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ServiceError) as error:
        client.save_configuration(section="policy", document=document, expected_hash=before["policy_hash"])
    assert error.value.code == "invalid_input"
    assert client.policy_configuration()["policy_hash"] == before["policy_hash"]


def test_missing_billing_template_and_invalid_patterns_reject_save(client):
    before = client.policy_configuration()
    content = deepcopy(before["content"])
    del content["templates"]["billing_reminder"]["stages"]["1"]
    with pytest.raises(ServiceError, match="template is required for every configured reminder stage"):
        client.save_configuration(section="content", document=content, expected_hash=before["content_hash"])
    content = deepcopy(before["content"])
    content["validation_patterns"]["prohibited_language"] = ["["]
    with pytest.raises(ServiceError, match="invalid patterns"):
        client.save_configuration(section="content", document=content, expected_hash=before["content_hash"])
    assert client.policy_configuration() == before


def test_audit_commit_failure_restores_exact_original_file(client, monkeypatch):
    before, document = edit_policy(client)
    original = policy_admin.POLICY_PATH.read_bytes()
    with client.sessions() as session:
        session_type = type(session)
    monkeypatch.setattr(session_type, "commit", lambda _: (_ for _ in ()).throw(SQLAlchemyError("test failure")))
    with pytest.raises(ServiceError, match="could not be loaded or saved"):
        client.save_configuration(section="policy", document=document, expected_hash=before["policy_hash"])
    assert policy_admin.POLICY_PATH.read_bytes() == original
    with client.sessions() as session:
        assert not session.scalars(select(AuditEvent).where(AuditEvent.event_type == "configuration_saved")).all()


def test_offer_save_publishes_new_version_and_keeps_historical_terms(client):
    before = client.policy_configuration()
    source = next(item for item in before["offers"] if item["active"])
    details = deepcopy(source["details"])
    details["terms"] = "Illustrative revised terms. No actual provisioning."
    after = client.save_offer(offer_id=source["id"], details=details, active=True, expected_hash=before["catalog_hash"])
    versions = [offer for offer in after["offers"] if offer["code"] == source["code"]]
    latest = max(versions, key=lambda offer: offer["version"])
    assert latest["version"] == max(item["version"] for item in before["offers"] if item["code"] == source["code"]) + 1
    assert latest["details"] == details
    assert [offer["id"] for offer in versions if offer["active"]] == [latest["id"]]
    old = next(offer for offer in versions if offer["id"] == source["id"])
    assert old["details"] == source["details"] and old["active"] is False
    with client.sessions() as session:
        event = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "offer_version_published")).one()
        assert event.details["offer_id"] == latest["id"]
    with pytest.raises(ServiceError, match="changed since"):
        client.save_offer(offer_id=source["id"], details=details, active=False, expected_hash=before["catalog_hash"])


@pytest.mark.parametrize("field,value", [
    ("synthetic", 1), ("channel", "sms"), ("redemption_days", True),
    ("eligible_plans", []), ("eligible_plans", ["A", "A"]),
    ("eligible_plans", [None]), ("expires_at", "2027-01-01T00:00:00"),
    ("terms", ""), ("hidden_rule", "unsupported"),
])
def test_invalid_offer_is_rejected_without_new_version(client, field, value):
    before = client.policy_configuration()
    source = before["offers"][0]
    details = deepcopy(source["details"])
    details[field] = value
    with pytest.raises(ServiceError) as error:
        client.save_offer(offer_id=source["id"], details=details, active=True, expected_hash=before["catalog_hash"])
    assert error.value.code == "invalid_input"
    assert client.policy_configuration()["catalog_hash"] == before["catalog_hash"]


@pytest.mark.parametrize('rules', [[], [{'min_overdue_days': 1, 'min_gap_days': 0, 'tone': 'courteous'}],
    [{'min_overdue_days': 1, 'min_gap_days': 7, 'tone': 'firm'}],
    [{'min_overdue_days': 1, 'min_gap_days': 7, 'tone': 'courteous'},
     {'min_overdue_days': 1, 'min_gap_days': 7, 'tone': 'direct'}]])
def test_invalid_progression_cannot_be_saved(client, rules):
    before = client.policy_configuration()
    document = deepcopy(before['policy'])
    document['reminders'] = rules
    with pytest.raises(ServiceError):
        client.save_configuration(section='policy', document=document, expected_hash=before['policy_hash'])
    assert client.policy_configuration()['policy_hash'] == before['policy_hash']
