from datetime import datetime, timezone
import pytest
from ui.labels import ist_time
from ui import streamlit_app as reviewer
from test_ui import FakeStreamlit


@pytest.mark.parametrize('value,expected', [
    ('2026-11-17T10:15:09.175210Z', '2026-11-17 15:45:09 IST'),
    ('2026-11-17T23:50:00+00:00', '2026-11-18 05:20:00 IST'),
    ('2026-11-17T15:45:09+05:30', '2026-11-17 15:45:09 IST'),
    ('2026-11-17T10:15:09', '2026-11-17 15:45:09 IST'),
    (datetime(2026, 11, 17, 10, 15, 9, tzinfo=timezone.utc), '2026-11-17 15:45:09 IST'),
    (None, '—'), ('invalid', 'Unknown time'),
])
def test_display_time_handles_offsets_and_date_rollover(value, expected):
    assert ist_time(value) == expected


def test_audit_and_delivery_tables_convert_utc_without_mutating_records(monkeypatch):
    from copy import deepcopy
    run = {'audit': [{'created_at': '2026-11-17T10:15:09Z', 'event_type': 'evaluated'}],
           'deliveries': [{'created_at': '2026-11-17T23:50:00Z', 'delivered_at': None}]}
    original = deepcopy(run)
    ui = FakeStreamlit()
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_audit(run)
    tables = [entry[1][0] for entry in ui.outputs if entry[0] == 'dataframe']
    assert tables[0][0]['Created'] == '2026-11-18 05:20:00 IST'
    assert tables[0][0]['Delivered'] == 'Not delivered'
    assert tables[1][0]['Time'] == '2026-11-17 15:45:09 IST'
    assert run == original
