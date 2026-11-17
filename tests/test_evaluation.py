from copy import deepcopy
from pathlib import Path
import hashlib
import json

import pytest

from scripts.evaluate_submission import (
    STATUSES, classification_metrics, confusion_matrix, independent_fact_checks,
    load_fixtures, measure_messages,
    summarize_usefulness,
)
from app.messages import generate_message
from app.policy import SEGMENTS, evaluate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def fixtures():
    customers, snapshots, expected, _ = load_fixtures(ROOT / "datasets/telecom_5000")
    # The first 25 records cover all independently authored scenarios.
    predictions = []
    for customer, snapshot in zip(customers[:25], snapshots[:25]):
        actual = evaluate(snapshot)
        predictions.append({"customer_id": customer["id"], "scenario": customer["scenario"],
                            "segments": {item["segment"]: item["status"] for item in actual["segments"]},
                            "strategy": actual["strategy"]})
    return snapshots[:25], predictions, {p["customer_id"]: expected[p["customer_id"]] for p in predictions}


def test_confusion_matrix_axes_and_unknown_class_are_explicit():
    measured = confusion_matrix(["match", "match", "no_match", "insufficient_evidence"],
                                ["match", "no_match", "match", "insufficient_evidence"], STATUSES)
    assert measured["counts"] == [[1, 1, 0], [1, 0, 0], [0, 0, 1]]
    assert measured["exact_agreement"] == 0.5
    assert measured["row_axis"] == "expected"
    assert measured["column_axis"] == "predicted"


def test_metrics_detect_real_misclassifications_instead_of_copying_truth(fixtures):
    _, predictions, expected = fixtures
    # The saved labels describe the historical policy. Current simple rules
    # can already disagree; the metric must still detect an additional error.
    baseline, _ = classification_metrics(expected, predictions)
    altered = deepcopy(predictions)
    first = altered[0]
    first["segments"]["loyalty_candidate"] = "no_match"
    first["strategy"]["objective"] = "no_outreach"
    metrics, wrong = classification_metrics(expected, altered)
    assert metrics["segments"]["loyalty_candidate"]["match_class"]["fn"] == baseline["segments"]["loyalty_candidate"]["match_class"]["fn"] + 1
    assert metrics["segments"]["loyalty_candidate"]["match_class"]["recall"] < 1
    assert metrics["strategy"]["correct"] == baseline["strategy"]["correct"] - 1
    finding = next(item for item in wrong if item["customer_id"] == first["customer_id"])
    assert finding["expected_objective"] == "offer_or_reward"
    assert finding["actual_strategy"]["objective"] == "no_outreach"


def test_unknowns_are_counted_separately_from_negative_predictions(fixtures):
    _, predictions, expected = fixtures
    metrics, _ = classification_metrics(expected, predictions)
    for segment in SEGMENTS:
        measured = metrics["segments"][segment]
        assert len(measured["counts"]) == 3
        assert measured["unknown_counts"]["expected"] > 0
        assert measured["unknown_counts"]["predicted"] == sum(
            prediction["segments"][segment] == "insufficient_evidence" for prediction in predictions)


def test_identity_mismatch_cannot_produce_a_report(fixtures):
    _, predictions, expected = fixtures
    with pytest.raises(ValueError, match="identities differ"):
        classification_metrics(expected, predictions[:-1])


def test_template_checks_measure_actual_actionable_drafts_only(fixtures):
    snapshots, predictions, _ = fixtures
    summary, findings, samples = measure_messages(snapshots, predictions)
    actionable = [item for item in predictions if item["strategy"]["objective"] in {
        "offer_or_reward", "billing_reminder", "appreciation_without_offer"}]
    assert summary["attempted"] == summary["generated"] == len(actionable)
    assert summary["independent_checks_passed"] == len(actionable)
    assert summary["fact_slots_expected"] == summary["fact_slots_matched"] > 0
    sample_scenarios = {"appreciation", "loyalty_offer", "decline_offer", "dormant_offer", "billing_first", "billing_stage3"}
    assert {sample["scenario"] for sample in samples} == {item["scenario"] for item in actionable} & sample_scenarios
    assert all(item["provider"] == "template" for item in findings)
    assert all(item["assessment"] is None for item in samples)


def test_independent_raw_fact_checks_reject_changed_amount(fixtures):
    snapshots, predictions, _ = fixtures
    snapshot, prediction = next((s, p) for s, p in zip(snapshots, predictions) if p["scenario"] == "billing_first")
    draft = generate_message(snapshot, prediction["strategy"], provider="template")
    invoice = next(i for i in snapshot["context"]["invoices"] if i["id"] == prediction["strategy"]["invoice_id"])
    draft["body"] = draft["body"].replace("Outstanding amount: INR " + invoice["outstanding"], "Outstanding amount: INR 9999.00")
    result = independent_fact_checks(snapshot, prediction["strategy"], draft)
    assert result["passed"] is False
    assert result["checks"]["raw_fact_slots_exact"] is False
    assert result["checks"]["no_extra_numeric_claims"] is False


def test_independent_checks_detect_source_fact_change_after_draft(fixtures):
    snapshots, predictions, _ = fixtures
    snapshot, prediction = next((s, p) for s, p in zip(snapshots, predictions) if p["scenario"] == "loyalty_offer")
    draft = generate_message(snapshot, prediction["strategy"], provider="template")
    modified = deepcopy(snapshot)
    modified["context"]["permissions"]["marketing"] = "denied"
    result = independent_fact_checks(modified, prediction["strategy"], draft)
    assert result["checks"]["raw_fact_slots_exact"] is True
    assert result["checks"]["selected_action_preconditions"] is False
    assert result["passed"] is False


def test_usefulness_summary_requires_exact_draft_and_automated_provenance(fixtures, tmp_path):
    snapshots, predictions, _ = fixtures
    _, _, samples = measure_messages(snapshots, predictions)
    sample = samples[0]
    content = {"subject": sample["draft"]["subject"], "body": sample["draft"]["body"]}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assessment = {"customer_id": sample["customer_id"], "scenario": sample["scenario"], "message_sha256": digest,
                  "reviewer_source": "automated", "scores": {key: 4 for key in ("relevance", "clarity", "personalization", "call_to_action", "tone")}}
    path = tmp_path / "assessments.json"
    path.write_text(json.dumps({"assessments": [assessment]}))
    result = summarize_usefulness(samples, path)
    assert result["overall_mean"] == 4
    assert result["human_study_performed"] is False
    assessment["message_sha256"] = "not-the-same-draft"
    path.write_text(json.dumps({"assessments": [assessment]}))
    with pytest.raises(ValueError, match="exact sample draft"):
        summarize_usefulness(samples, path)
