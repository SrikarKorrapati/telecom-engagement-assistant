"""Durable hosted settings, conflict detection, and transactional audit writes."""

from copy import deepcopy
import os
import subprocess
import sys

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app import configuration_store, policy_admin
from app.config import Settings
from app.content import load_content
from app.db import initialize_database, make_engine
from app.models import AuditEvent
from app.policy import load_policy
from app.service import EngagementService, ServiceError


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "hosted.db")
    monkeypatch.setenv("POLICY_STORAGE", "database")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(policy_admin, "LOCK_PATH", tmp_path / "lock")
    # These paths intentionally do not exist: cloud edits must not depend on files.
    monkeypatch.setattr(policy_admin, "POLICY_PATH", tmp_path / "missing-policy.json")
    monkeypatch.setattr(policy_admin, "CONTENT_PATH", tmp_path / "missing-content.json")
    engine = make_engine(url)
    initialize_database(engine)
    configuration_store.initialize(engine)
    service = EngagementService(Settings(url), engine)
    yield service
    engine.dispose()
    configuration_store._engine(url).dispose()
    configuration_store._engine.cache_clear()


def test_hosted_save_is_read_by_evaluation_and_survives_restart(client):
    before = client.policy_configuration()
    policy = deepcopy(before["policy"])
    policy["segments"]["loyalty_tenure_months"] = 48
    saved = client.save_configuration(section="policy", document=policy, expected_hash=before["policy_hash"])
    content = deepcopy(before["content"])
    content["signature"] = "Persisted synthetic team"
    client.save_configuration(section="content", document=content, expected_hash=before["content_hash"])
    assert load_policy()["segments"]["loyalty_tenure_months"] == 48
    assert load_content()["signature"] == "Persisted synthetic team"
    configuration_store.initialize(client.engine)
    assert client.policy_configuration()["policy_hash"] == saved["policy_hash"]
    result = subprocess.run([sys.executable, "-c", """
from app.policy import load_policy
p = load_policy()
assert p['segments']['loyalty_tenure_months'] == 48
assert p['content']['signature'] == 'Persisted synthetic team'
"""], env=os.environ.copy(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with client.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_stale_hosted_edit_is_rejected_without_overwriting(client):
    before = client.policy_configuration()
    policy = deepcopy(before["policy"])
    policy["segments"]["loyalty_tenure_months"] = 24
    client.save_configuration(section="policy", document=policy, expected_hash=before["policy_hash"])
    policy["segments"]["loyalty_tenure_months"] = 48
    with pytest.raises(ServiceError, match="changed"):
        client.save_configuration(section="policy", document=policy, expected_hash=before["policy_hash"])
    assert load_policy()["segments"]["loyalty_tenure_months"] == 24


def test_failed_commit_rolls_back_settings_and_audit(client, monkeypatch):
    before = client.policy_configuration()
    policy = deepcopy(before["policy"])
    policy["segments"]["loyalty_tenure_months"] = 48

    def fail_commit(self):
        raise SQLAlchemyError("Synthetic commit failure")

    monkeypatch.setattr(client.sessions.class_, "commit", fail_commit)
    with pytest.raises(ServiceError):
        client.save_configuration(section="policy", document=policy, expected_hash=before["policy_hash"])
    assert client.policy_configuration()["policy_hash"] == before["policy_hash"]
    with client.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
