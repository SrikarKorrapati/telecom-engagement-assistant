"""Hosted access boundaries and persisted configuration across process restarts."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from app.access import demo_password
from app.db import make_engine
from app.paths import initialize_policy_directory
from ui import dataset_dashboard, streamlit_app
from ui.access import require_access
from test_ui import FakeStreamlit, Rerun


PASSWORD = "synthetic-test-password-123456"


@pytest.fixture(autouse=True)
def access_environment(monkeypatch):
    for key in ("DEMO_ACCESS_PASSWORD", "REQUIRE_DEMO_LOGIN", "RENDER", "POLICY_DATA_DIR", "POLICY_STORAGE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("app.access.load_dotenv", lambda **kwargs: None)


@pytest.mark.parametrize("flag", ["RENDER", "REQUIRE_DEMO_LOGIN"])
@pytest.mark.parametrize("password", ["", "short"])
def test_hosted_access_fails_closed(monkeypatch, flag, password):
    monkeypatch.setenv(flag, "true")
    monkeypatch.setenv("DEMO_ACCESS_PASSWORD", password)
    with pytest.raises(ValueError, match="16 characters"):
        demo_password()


@pytest.mark.parametrize("module", [streamlit_app, dataset_dashboard])
def test_unauthenticated_visitors_never_reach_application(monkeypatch, module):
    monkeypatch.setenv("DEMO_ACCESS_PASSWORD", PASSWORD)
    ui = FakeStreamlit(choices={"Workspace": "Policies"})
    service = Mock(side_effect=AssertionError("Accessed application before login"))
    monkeypatch.setattr(module, "st", ui)
    monkeypatch.setattr(module, "get_service", service)
    with pytest.raises(Rerun):
        module.main()
    service.assert_not_called()
    assert "Sign in" in ui.buttons


class LoginUI(FakeStreamlit):
    def form_submit_button(self, name, **kwargs):
        self.buttons[name] = kwargs
        if name == self.press:
            kwargs["on_click"]()
        return name == self.press


def test_login_logout_and_password_rotation(monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_PASSWORD", PASSWORD)
    ui = LoginUI(press="Sign in")
    ui.session_state["_demo_password"] = "wrong"
    with pytest.raises(Rerun):
        require_access(ui)
    assert "_demo_access" not in ui.session_state
    assert "_demo_password" not in ui.session_state

    ui.session_state["_demo_password"] = PASSWORD
    with pytest.raises(Rerun):
        require_access(ui)
    ui.press = None
    require_access(ui)
    assert PASSWORD not in str(ui.session_state)
    saved_session = dict(ui.session_state)

    ui.press = "Sign out"
    with pytest.raises(Rerun):
        require_access(ui)
    assert not ui.session_state

    ui.session_state.update(saved_session)
    monkeypatch.setenv("DEMO_ACCESS_PASSWORD", PASSWORD + "-rotated")
    ui.press = None
    with pytest.raises(Rerun):
        require_access(ui)


def test_local_access_is_unchanged_without_password():
    require_access(FakeStreamlit())


def test_policy_edits_survive_new_process_and_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_DATA_DIR", str(tmp_path))
    initialize_policy_directory()
    policy = json.loads((tmp_path / "policy.json").read_text())
    policy["segments"]["loyalty_tenure_months"] = 48
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    content = json.loads((tmp_path / "content.json").read_text())
    content["signature"] = "Persistent synthetic care team"
    (tmp_path / "content.json").write_text(json.dumps(content))
    script = """
from app.paths import initialize_policy_directory
from app.policy import load_policy
from app.policy_admin import POLICY_PATH, CONTENT_PATH
import json
initialize_policy_directory()
policy = load_policy()
assert policy['segments']['loyalty_tenure_months'] == 48
assert policy['content']['signature'] == 'Persistent synthetic care team'
assert json.loads(POLICY_PATH.read_text())['segments']['loyalty_tenure_months'] == 48
assert json.loads(CONTENT_PATH.read_text())['signature'] == 'Persistent synthetic care team'
"""
    result = subprocess.run([sys.executable, "-c", script], env=os.environ.copy(),
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_missing_persistent_file_does_not_reset_existing_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_DATA_DIR", str(tmp_path))
    (tmp_path / "policy.json").write_text("invalid saved content")
    initialize_policy_directory()
    assert (tmp_path / "policy.json").read_text() == "invalid saved content"
    assert (tmp_path / "content.json").is_file()
    from app.policy import load_policy
    with pytest.raises(ValueError):
        load_policy()


def test_render_legacy_database_scheme_selects_installed_driver():
    engine = make_engine("postgres://demo:synthetic@localhost/example")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg2"
    finally:
        engine.dispose()
