"""Hold regression cases use isolated data and never call a paid model."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import workflow
from app.config import Settings
from app.db import initialize_database, make_engine, make_session_factory
from app.models import Customer, DeliveryJob, EngagementRun, MessageRevision, AuditEvent
from app.policy import evaluate, load_policy
from app.seed import demo_id, seed_demo
from app.service import EngagementService, ServiceError
from test_policy import NOW, iso, overdue, snapshot


def recorded(snapshot, invoice, gaps):
    snapshot['context']['reminder_history'] = [
        {'invoice_id': invoice['id'], 'stage': stage, 'sent_at': iso(NOW - timedelta(days=gap))}
        for stage, gap in enumerate(gaps, 1)]
    snapshot['recent_deliveries'] = [
        {'created_at': item['sent_at'], 'purpose': 'billing',
         'idempotency_key': f"{snapshot['customer_id']}:billing:{invoice['id']}:{item['stage']}"}
        for item in snapshot['context']['reminder_history']]


@pytest.mark.parametrize('gaps,objective,stage,tone', [
    ([], 'billing_reminder', 1, 'courteous'),
    ([7], 'billing_reminder', 2, 'direct'),
    ([6.99999], 'hold', 2, None),
    ([15, 7], 'billing_reminder', 3, 'firm'),
    ([22, 14, 7], 'no_outreach', 3, None),
])
def test_verified_progression_advances_once_and_respects_gap(snapshot, gaps, objective, stage, tone):
    invoice = overdue(snapshot, days=40)
    recorded(snapshot, invoice, gaps)
    strategy = evaluate(snapshot)['strategy']
    assert (strategy['objective'], strategy['reminder_stage'], strategy['tone']) == (objective, stage, tone)
    if objective == 'billing_reminder':
        assert strategy['idempotency_key'].endswith(f":{stage}")
    elif objective == 'hold':
        assert strategy['next_eligible_at']
    else:
        assert strategy['reason_code'] == 'outreach_completed'
        assert strategy['idempotency_key'] is None


def test_progression_uses_configured_overdue_threshold_gap_and_cap(snapshot):
    invoice = overdue(snapshot, days=40)
    recorded(snapshot, invoice, [7])
    policy = load_policy()
    policy['reminders'][1]['min_gap_days'] = 8
    assert evaluate(snapshot, policy)['strategy']['reason_code'] == 'reminder_not_due'
    policy['reminders'][1]['min_gap_days'] = 7
    policy['reminders'][1]['min_overdue_days'] = 41
    policy['reminders'][2]['min_overdue_days'] = 50
    assert evaluate(snapshot, policy)['strategy']['reason_code'] == 'reminder_not_due'
    policy['reminders'] = policy['reminders'][:1]
    assert evaluate(snapshot, policy)['strategy']['reason_code'] == 'outreach_completed'


@pytest.mark.parametrize('case', ['missing_send', 'missing_stage', 'duplicate_stage', 'future',
                                 'reversed', 'premature', 'early_gap', 'extra_send'])
def test_inconsistent_reminder_evidence_never_escalates(snapshot, case):
    invoice = overdue(snapshot, days=40)
    recorded(snapshot, invoice, [16, 8])
    history = snapshot['context']['reminder_history']
    if case == 'missing_send':
        snapshot['recent_deliveries'].pop()
    elif case == 'missing_stage':
        history.pop(0)
    elif case == 'duplicate_stage':
        history.append(deepcopy(history[0]))
    elif case == 'future':
        history[-1]['sent_at'] = iso(NOW + timedelta(seconds=1))
    elif case == 'reversed':
        recorded(snapshot, invoice, [8, 16])
    elif case == 'premature':
        recorded(snapshot, invoice, [39, 33])  # stage 2 was only 7 days overdue
    elif case == 'early_gap':
        recorded(snapshot, invoice, [10, 8])
    elif case == 'extra_send':
        snapshot['recent_deliveries'].append({'created_at': iso(NOW - timedelta(days=7)),
            'idempotency_key': f"{snapshot['customer_id']}:billing:{invoice['id']}:3"})
    assert evaluate(snapshot)['strategy']['reason_code'] == 'reminder_history_unknown'


@pytest.mark.parametrize('case', ['consent', 'dispute', 'stale', 'frequency', 'paid'])
def test_later_stage_preserves_contact_and_billing_safety(snapshot, case):
    invoice = overdue(snapshot, days=40)
    recorded(snapshot, invoice, [8])
    if case == 'consent':
        snapshot['context']['permissions']['billing'] = 'denied'
    elif case == 'dispute':
        invoice['disputed'] = True
    elif case == 'stale':
        snapshot['context']['source_as_of'] = iso(NOW - timedelta(hours=49))
    elif case == 'frequency':
        snapshot['recent_deliveries'].append({'created_at': iso(NOW - timedelta(days=1)),
            'idempotency_key': 'another-outreach', 'purpose': 'marketing'})
    elif case == 'paid':
        invoice.update(outstanding='0.00', status='paid', paid_full_at=iso(NOW))
    strategy = evaluate(snapshot)['strategy']
    assert strategy['objective'] != 'billing_reminder'
    if case != 'paid':
        assert strategy['objective'] == 'hold'


def test_completed_outreach_does_not_hide_malformed_history_or_lost_consent(snapshot):
    key = evaluate(snapshot)['strategy']['idempotency_key']
    snapshot['recent_deliveries'] = [{'created_at': iso(NOW - timedelta(days=10)), 'idempotency_key': key}, {}]
    assert evaluate(snapshot)['strategy']['reason_code'] == 'delivery_history_unknown'
    snapshot['recent_deliveries'].pop()
    assert evaluate(snapshot)['strategy']['reason_code'] == 'outreach_completed'
    snapshot['context']['permissions']['marketing'] = 'denied'
    assert evaluate(snapshot)['strategy']['reason_code'] == 'contact_permission_not_allowed'


@pytest.fixture
def client(monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(workflow, 'utcnow', lambda: clock[0])
    monkeypatch.setattr('app.service.utcnow', lambda: clock[0])
    engine = make_engine('sqlite://')
    initialize_database(engine)
    with make_session_factory(engine).begin() as session:
        seed_demo(session, NOW)
    with EngagementService(Settings('sqlite://'), engine) as service:
        yield service, clock
    engine.dispose()


def counts(service):
    with service.sessions() as session:
        return [session.scalar(select(func.count()).select_from(model))
                for model in (EngagementRun, MessageRevision, DeliveryJob, AuditEvent)]


def prepare(service, customer_id):
    run = service.create_run(customer_id)
    return service.draft(run['id'], provider='template')


@pytest.mark.parametrize('scenario', ['loyalty', 'appreciation', 'billing'])
def test_repeated_evaluation_reopens_real_delivery_without_new_run_or_send(client, scenario):
    service, _ = client
    customer_id = demo_id('customer:' + scenario)
    run = prepare(service, customer_id)
    service.review(run['latest_revision_id'], action='approve')
    delivered = service.deliver(run['latest_revision_id'])
    before = counts(service)
    for _ in range(3):
        again = service.create_run(customer_id)
        assert again['id'] == delivered['id']
        assert again['state'] == 'delivered'
        assert again['revisions'] == delivered['revisions']
        assert again['reviews'] == delivered['reviews']
        assert again['deliveries'] == delivered['deliveries']
        with pytest.raises(ServiceError, match='closed'):
            service.draft(again['id'], provider='template')
    after = counts(service)
    assert after[:3] == before[:3]
    assert after[3] == before[3] + 3
    assert again['audit'][-1]['event_type'] == 'completed_outreach_reopened'


def test_repeat_after_opt_out_still_holds(client):
    service, _ = client
    customer_id = demo_id('customer:loyalty')
    run = prepare(service, customer_id)
    service.review(run['latest_revision_id'], action='approve')
    service.deliver(run['latest_revision_id'])
    service.simulate(customer_id, event='opt_out')
    assert service.create_run(customer_id)['state'] == 'held'
    assert counts(service)[2] == 1


def test_three_stages_require_fresh_evidence_separate_approval_and_unique_keys(client):
    service, clock = client
    customer_id = demo_id('customer:billing')
    run_ids, keys = [], []
    for stage in (1, 2, 3):
        if stage > 1:
            clock[0] += timedelta(days=8)
            assert service.create_run(customer_id)['strategy']['objective'] == 'hold'  # source is stale
            with service.sessions.begin() as session:
                customer = session.get(Customer, customer_id)
                context = deepcopy(customer.context)
                context['source_as_of'] = iso(clock[0])
                customer.context = context
                customer.version += 1
        run = prepare(service, customer_id)
        assert run['strategy']['reminder_stage'] == stage
        with pytest.raises(ServiceError, match='Approve'):
            service.deliver(run['latest_revision_id'])
        service.review(run['latest_revision_id'], action='approve')
        delivered = service.deliver(run['latest_revision_id'])
        assert service.deliver(run['latest_revision_id'])['deliveries'] == delivered['deliveries']
        assert service.create_run(customer_id)['id'] == run['id']
        run_ids.append(run['id'])
        keys.append(delivered['deliveries'][0]['idempotency_key'])
        assert counts(service)[2] == stage
    assert len(set(run_ids)) == len(set(keys)) == 3
    assert service.create_run(customer_id)['state'] == 'delivered'
    with service.sessions() as session:
        history = session.get(Customer, customer_id).context['reminder_history']
        assert [item['stage'] for item in history] == [1, 2, 3]


def test_demo_selection_is_current_read_only_and_excludes_contacted_customers(client):
    service, _ = client
    before = counts(service)
    examples = service.demo_customers()
    assert len(examples) == 5
    assert {item['demo_case'] for item in examples} == {
        'Consistent customer', 'Loyalty offer', 'Declining activity', 'Dormant customer', 'Overdue bill'}
    assert counts(service) == before
    customer_id = demo_id('customer:loyalty')
    run = prepare(service, customer_id)
    service.review(run['latest_revision_id'], action='approve')
    service.deliver(run['latest_revision_id'])
    assert customer_id not in {item['id'] for item in service.demo_customers()}
    for scenario in ('opt_out', 'stale', 'dispute', 'missing'):
        assert service.get_customer(demo_id('customer:' + scenario))
        assert service.create_run(demo_id('customer:' + scenario))['state'] == 'held'


def test_ui_friendly_selection_and_completed_history(monkeypatch):
    from unittest.mock import Mock
    from test_ui import FakeStreamlit
    from ui import streamlit_app as ui
    service, fake = Mock(spec=EngagementService), FakeStreamlit()
    service.config.return_value = {'delivery_mode': 'mock'}
    service.list_customers.return_value = [{'id': key, 'name': key} for key in ['contacted', 'ready', 'refusal']]
    service.demo_customers.return_value = [{'id': 'ready', 'demo_case': 'Loyalty offer'}]
    service.list_runs.return_value = []
    service.get_customer.return_value = {'id': 'ready', 'name': 'ready', 'context': {}}
    monkeypatch.setattr(ui, 'st', fake)
    monkeypatch.setattr(ui, 'get_service', lambda: service)
    ui.main()
    assert fake.session_state['customer_picker'] == 'ready'
    service.create_run.assert_not_called()
    assert ui.state_label({'state': 'held', 'strategy': {'reason_code': 'duplicate_outreach'}}) == 'Previously delivered'
    assert ui.state_label({'state': 'held', 'strategy': {'reason_code': 'contact_permission_not_allowed'}}) == 'On hold'


def test_ui_completed_delivery_opens_success_and_next_eligible_without_send(monkeypatch, client):
    from unittest.mock import Mock
    from test_ui import FakeStreamlit, Rerun
    from ui import streamlit_app as ui
    service, _ = client
    customer_id = demo_id('customer:loyalty')
    run = prepare(service, customer_id)
    service.review(run['latest_revision_id'], action='approve')
    service.deliver(run['latest_revision_id'])
    spy = Mock(spec=EngagementService, wraps=service)
    fake = FakeStreamlit(choices={'Customer selection': 'All customers', 'Customer': customer_id})
    monkeypatch.setattr(ui, 'st', fake)
    monkeypatch.setattr(ui, 'get_service', lambda: spy)
    fake.press = 'Evaluate customer'
    with pytest.raises(Rerun):
        ui.main()
    assert fake.session_state['active_run_id'] == run['id']
    assert 'completed mock delivery' in fake.session_state['flash'][1]
    fake.press = None
    ui.main()
    assert 'Choose another eligible customer' in fake.buttons
    assert 'Download approved mock output' in fake.buttons
    assert 'Approve exact revision' not in fake.buttons
    assert not any(item[0] == 'warning' for item in fake.outputs)
    spy.deliver.assert_not_called()
    spy.draft.assert_not_called()
    fake.press = 'Choose another eligible customer'
    with pytest.raises(Rerun):
        ui.main()
    assert fake.session_state['pending_navigation']['customer_id'] != customer_id
    assert fake.session_state['pending_navigation']['scope'] == 'Ready for demo'


def test_synthetic_completed_fixture_is_closed_without_inventing_a_local_delivery(client):
    service, _ = client
    customer_id = demo_id('customer:loyalty')
    with service.sessions.begin() as session:
        customer = session.get(Customer, customer_id)
        snapshot = workflow.snapshot_for(session, customer, NOW)
        key = evaluate(snapshot)['strategy']['idempotency_key']
        context = deepcopy(customer.context)
        context['fixture_delivery_history'] = [{'created_at': iso(NOW - timedelta(days=10)), 'idempotency_key': key}]
        customer.context = context
    run = service.create_run(customer_id)
    assert run['state'] == 'no_outreach'
    assert run['strategy']['reason_code'] == 'outreach_completed'
    assert run['deliveries'] == []
    with pytest.raises(ServiceError, match='closed'):
        service.draft(run['id'], provider='template')
    assert counts(service)[2] == 0
