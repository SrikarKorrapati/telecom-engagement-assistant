"""Dashboard interactions without model calls or live catalogue changes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from app.policy import load_policy
from app.service import EngagementService, ServiceError
from ui import policy_dashboard as dashboard
from ui import streamlit_app as reviewer
from test_ui import FakeStreamlit, Rerun


class PolicyUI(FakeStreamlit):
    def number_input(self, name, value=0, **kwargs):
        return self.choices.get(name, value)

    def date_input(self, name, value=None, **kwargs):
        return self.choices.get(name, value)

    time_input = date_input


@pytest.fixture
def setup(monkeypatch):
    policy = load_policy()
    content = policy.pop("content")
    snapshot = {"policy": policy, "content": content, "policy_hash": "policy-hash",
                "content_hash": "content-hash", "catalog_hash": "catalog-hash",
                "offers": json.loads(Path("datasets/telecom_5000/offers.json").read_text())}
    service = Mock(spec=EngagementService)
    service.policy_configuration.return_value = snapshot
    ui = PolicyUI()
    monkeypatch.setattr(dashboard, "st", ui)
    return ui, service, snapshot


def test_render_shows_sections_without_mutation(setup):
    ui, service, snapshot = setup
    original = deepcopy(snapshot)
    dashboard.render_policy_dashboard(service)
    service.save_configuration.assert_not_called()
    service.save_offer.assert_not_called()
    assert snapshot == original
    tables = [entry[1][0] for entry in ui.outputs if entry[0] == "dataframe"]
    assert len(tables) == 1 and len(tables[0]) == len(snapshot["offers"])
    assert any(entry[0] == "markdown" and "| Consistent |" in str(entry) and "| Overdue bill |" in str(entry) for entry in ui.outputs)
    assert "Save rules" in ui.buttons and "Save language rules" in ui.buttons


def test_rule_save_uses_original_hash_and_preserves_other_rules(setup):
    ui, service, snapshot = setup
    ui.press = "Save rules"
    ui.choices = {"Loyalty tenure · months": 24}
    with pytest.raises(Rerun):
        dashboard._segments(service, snapshot)
    payload = service.save_configuration.call_args.kwargs
    assert payload["expected_hash"] == "policy-hash" and payload["section"] == "policy"
    assert payload["document"]["segments"]["loyalty_tenure_months"] == 24
    assert payload["document"]["version"] == snapshot["policy"]["version"]
    assert snapshot["policy"]["segments"]["loyalty_tenure_months"] == 12


def test_conflict_is_visible_without_success_notice(setup):
    ui, service, snapshot = setup
    ui.press = "Save rules"
    service.save_configuration.side_effect = ServiceError("Configuration changed. Refresh before saving.")
    dashboard._segments(service, snapshot)
    assert "policy-admin-notice" not in ui.session_state
    assert any(entry[0] == "error" and "Refresh" in str(entry) for entry in ui.outputs)


def test_message_save_targets_selected_billing_stage(setup):
    ui, service, snapshot = setup
    ui.press = "Save message"
    ui.choices = {"Message purpose": "billing_reminder",
                  "Subject": "Please review your bill"}
    with pytest.raises(Rerun):
        dashboard._content(service, snapshot)
    payload = service.save_configuration.call_args.kwargs
    assert payload["section"] == "content" and payload["expected_hash"] == "content-hash"
    templates = payload["document"]["templates"]
    assert templates["billing_reminder"]["stages"]["1"]["subject"] == "Please review your bill"
    assert templates["offer_or_reward"] == snapshot["content"]["templates"]["offer_or_reward"]


def test_offer_save_sends_catalogue_hash_and_selected_version(setup):
    ui, service, snapshot = setup
    source = snapshot["offers"][1]
    ui.press = "Save new offer version"
    ui.choices = {"Offer version": source["id"], "Terms": "Revised synthetic offer terms."}
    with pytest.raises(Rerun):
        dashboard._offers(service, snapshot)
    payload = service.save_offer.call_args.kwargs
    assert payload["expected_hash"] == "catalog-hash" and payload["offer_id"] == source["id"]
    assert payload["details"]["terms"] == "Revised synthetic offer terms."
    assert payload["details"]["synthetic"] is True and payload["active"] is True


def test_refresh_discards_forms_without_saving(setup):
    ui, service, _ = setup
    ui.press = "Discard edits"
    with pytest.raises(Rerun):
        dashboard.render_policy_dashboard(service)
    assert ui.session_state["policy-admin-epoch"] == 1
    service.save_configuration.assert_not_called()
    service.save_offer.assert_not_called()


def test_policy_navigation_does_not_load_customer_workflow(monkeypatch, setup):
    ui, service, _ = setup
    ui.choices["Workspace"] = "Policies"
    monkeypatch.setattr(reviewer, "st", ui)
    monkeypatch.setattr(reviewer, "get_service", lambda: service)
    reviewer.main()
    service.policy_configuration.assert_called_once()
    service.list_customers.assert_not_called()
    service.create_run.assert_not_called()


def test_offer_status_checks_expiry_and_enabled_flag(setup):
    _, _, snapshot = setup
    now = datetime(2026, 11, 17, 6, 30, tzinfo=timezone.utc)
    assert [dashboard.offer_status(offer, now) for offer in snapshot["offers"]] == ["Expired", "Available", "Inactive"]
