"""Acceptance checks through the direct application service; no model/network calls."""
import pytest
from sqlalchemy import select
from app.config import Settings
from app.db import make_engine, make_session_factory, initialize_database
from app.service import EngagementService, ServiceError
from app.models import Customer, Offer, DeliveryJob
from app.seed import seed_demo, demo_id

@pytest.fixture
def client():
    engine = make_engine('sqlite://')
    initialize_database(engine)
    with make_session_factory(engine)() as session:
        seed_demo(session)
        session.commit()
    with EngagementService(Settings('sqlite://'), engine) as value:
        yield value
    engine.dispose()

def prepare(client, scenario='billing', approve=False):
    run = client.create_run(demo_id('customer:' + scenario))
    run = client.draft(run['id'], provider='template')
    assert run['revisions'][-1]['validation']['passed'], run['revisions'][-1]['validation']
    if approve:
        run = client.review(run['latest_revision_id'], action='approve')
    return run

@pytest.mark.parametrize('scenario,objective', [('billing', 'billing_reminder'), ('loyalty', 'offer_or_reward'), ('appreciation', 'appreciation_without_offer')])
def test_complete_mock_delivery(client, scenario, objective):
    run = prepare(client, scenario, approve=True)
    assert run['strategy']['objective'] == objective
    revision_id = run['latest_revision_id']
    run = client.deliver(revision_id)
    assert run['state'] == 'delivered'
    assert run['deliveries'][0]['result']['mode'] == 'mock'
    assert len(client.deliver(revision_id)['deliveries']) == 1
    assert any((e['event_type'] == 'mock_delivered' for e in run['audit']))

@pytest.mark.parametrize('scenario', ['missing', 'dispute', 'opt_out', 'stale'])
def test_held_customers_cannot_draft(client, scenario):
    run = client.create_run(demo_id('customer:' + scenario))
    assert run['state'] == 'held'
    assert len(run['segments']) == 5
    with pytest.raises(ServiceError) as caught:
        client.draft(run['id'], provider='template')
    assert caught.value.code == 'conflict'

def test_approval_required(client):
    run = prepare(client)
    with pytest.raises(ServiceError) as caught:
        client.deliver(run['latest_revision_id'])
    assert caught.value.code == 'conflict'

def test_edit_invalidates_approval_and_old_revision(client):
    run = prepare(client, approve=True)
    old = run['latest_revision_id']
    content = run['revisions'][-1]
    run = client.edit(old, subject=content['subject'], body=content['body'], feedback='Rechecked wording')
    assert run['state'] == 'drafted'
    assert run['latest_revision_id'] != old
    with pytest.raises(ServiceError) as caught:
        client.review(old, action='approve')
    assert caught.value.code == 'conflict'
    with pytest.raises(ServiceError) as caught:
        client.deliver(old)
    assert caught.value.code == 'conflict'
    with pytest.raises(ServiceError) as caught:
        client.deliver(run['latest_revision_id'])
    assert caught.value.code == 'conflict'

def test_invented_amount_blocks_approval(client):
    run = prepare(client)
    revision = run['revisions'][-1]
    run = client.edit(revision['id'], subject=revision['subject'], body=revision['body'] + '\nPay INR 99999.00 immediately.')
    assert run['state'] == 'validation_failed'
    with pytest.raises(ServiceError) as caught:
        client.review(run['latest_revision_id'], action='approve')
    assert caught.value.code == 'conflict'

@pytest.mark.parametrize('event', ['payment', 'opt_out', 'dispute'])
def test_changed_facts_block_approved_send(client, event):
    run = prepare(client, approve=True)
    client.simulate(run['customer_id'], event=event)
    with pytest.raises(ServiceError) as response:
        client.deliver(run['latest_revision_id'])
    assert response.value.code == 'conflict'
    saved = client.get_run(run['id'])
    assert saved['state'] == 'held'
    assert saved['blocked_reasons']
    assert not saved['deliveries']
    assert any((e['event_type'] == 'delivery_blocked' for e in saved['audit']))

def test_offer_changes_block_approved_send(client):
    run = prepare(client, 'loyalty', approve=True)
    with client.sessions() as session:
        offer = session.get(Offer, run['strategy']['offer_id'])
        offer.active = False
        session.commit()
    with pytest.raises(ServiceError) as caught:
        client.deliver(run['latest_revision_id'])
    assert caught.value.code == 'conflict'

def test_cross_run_duplicate_and_history(client):
    first = prepare(client, 'billing', approve=True)
    second = prepare(client, 'billing', approve=True)
    client.deliver(first['latest_revision_id'])
    with pytest.raises(ServiceError) as caught:
        client.deliver(second['latest_revision_id'])
    assert caught.value.code == 'conflict'
    with client.sessions() as session:
        assert len(session.scalars(select(DeliveryJob)).all()) == 1
        customer = session.get(Customer, first['customer_id'])
        assert customer.context['reminder_history'][-1]['stage'] == first['strategy']['reminder_stage']

def test_rejection_terminal_and_no_secrets(client):
    run = prepare(client)
    run = client.review(run['latest_revision_id'], action='reject', feedback='Do not contact')
    assert run['state'] == 'rejected'
    with pytest.raises(ServiceError) as caught:
        client.deliver(run['latest_revision_id'])
    assert caught.value.code == 'conflict'
    assert 'openai_api_key' not in str(client.config())

def test_dashboard_and_customer_contract(client):
    customer = client.list_customers()[0]
    assert 'context' not in customer
    assert 'context' in client.get_customer(customer['id'])
    prepare(client)
    dashboard = client.dashboard()
    assert dashboard['counts']['customers'] == 10
    assert dashboard['counts']['awaiting_review'] == 1
    assert dashboard['segments']
    assert dashboard['recent_activity']
    with pytest.raises(ServiceError) as caught:
        client.create_run('bad-id')
    assert caught.value.code == 'invalid_input'

def test_openai_unconfigured_is_explicit_error(client):
    run = client.create_run(demo_id('customer:loyalty'))
    with pytest.raises(ServiceError) as response:
        client.draft(run['id'], provider='openai')
    assert response.value.code == 'invalid_input'
    saved = client.get_run(run['id'])
    assert not saved['revisions']
    assert any((e['event_type'] == 'generation_failed' for e in saved['audit']))
