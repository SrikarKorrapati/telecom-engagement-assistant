from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import random
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from generate import SCENARIOS, build_customer, catalogue, generate
from validate import validate_record


@pytest.mark.parametrize("reference", [
    "2026-11-01T00:00:00+05:30",
    "2027-01-01T00:00:00+05:30",
    "2028-03-01T00:00:00+05:30",
    "2026-11-30T23:59:00+05:30",
    "2026-01-01T00:00:00+00:00",
])
def test_all_scenarios_remain_coherent_at_month_year_and_leap_boundaries(reference):
    now = datetime.fromisoformat(reference).astimezone(timezone.utc)
    offers = catalogue(now)
    for index, spec in enumerate(SCENARIOS, start=1):
        customer, context, label = build_customer(index, spec, now, random.Random(index), 17)
        validate_record(customer, context, label, offers)


def test_naive_reference_time_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        generate(tmp_path, datetime(2026, 11, 17), 17)


def test_same_seed_reproduces_records_and_changed_seed_changes_identity():
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    first = build_customer(1, SCENARIOS[0], now, random.Random(17), 17)
    same = build_customer(1, SCENARIOS[0], now, random.Random(17), 17)
    changed = build_customer(1, SCENARIOS[0], now, random.Random(18), 18)
    assert first == same
    assert first[0]["id"] != changed[0]["id"]


def test_independent_integrity_check_rejects_impossible_invoice_balance():
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    customer, context, label = build_customer(1, SCENARIOS[0], now, random.Random(17), 17)
    customer = deepcopy(customer)
    customer["context"]["invoices"][0]["outstanding"] = "999999.00"
    with pytest.raises(AssertionError):
        validate_record(customer, context, label, catalogue(now))


def test_independent_oracle_rejects_wrong_expected_segment():
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    customer, context, label = build_customer(1, SCENARIOS[0], now, random.Random(17), 17)
    label["segments"]["consistent"] = "no_match"
    with pytest.raises(AssertionError, match="independent segment oracle"):
        validate_record(customer, context, label, catalogue(now))
