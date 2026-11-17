"""Additional adversarial workflow checks and opt-in real PostgreSQL race tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from app.config import Settings
from app.db import make_engine, make_session_factory, initialize_database
from app.service import EngagementService, ServiceError
from app.models import MessageRevision, Offer, DeliveryJob
from app.seed import demo_id, seed_demo
from app import workflow

@pytest.fixture
def client():
    engine = make_engine('sqlite://')
    initialize_database(engine)
    with make_session_factory(engine)() as session:
        seed_demo(session)
        session.commit()
    with EngagementService(Settings('sqlite://'), engine) as result:
        yield result
    engine.dispose()

def approved(client, scenario='billing'):
    run = client.create_run(demo_id('customer:' + scenario))
    run = client.draft(run['id'], provider='template')
    return client.review(run['latest_revision_id'], action='approve')

def assert_held_without_delivery(client, run):
    with pytest.raises(ServiceError) as caught:
        client.deliver(run['latest_revision_id'])
    assert caught.value.code == 'conflict'
    saved = client.get_run(run['id'])
    assert saved['state'] == 'held'
    assert saved['blocked_reasons']
    assert saved['deliveries'] == []
    assert any((event['event_type'] == 'delivery_blocked' for event in saved['audit']))

def test_content_tampering_after_approval_is_durably_blocked(client):
    run = approved(client)
    with client.sessions() as session:
        revision = session.get(MessageRevision, run['latest_revision_id'])
        revision.body += '\nA changed sentence after approval.'
        session.commit()
    assert_held_without_delivery(client, run)

def test_unchanged_offer_identity_with_modified_terms_is_blocked(client):
    run = approved(client, 'loyalty')
    with client.sessions() as session:
        offer = session.get(Offer, run['strategy']['offer_id'])
        details = deepcopy(offer.details)
        details['terms'] += ' Updated approved terms.'
        offer.details = details
        session.commit()
    assert_held_without_delivery(client, run)

def test_policy_version_change_requires_fresh_approval(client, monkeypatch):
    run = approved(client)
    changed = workflow.load_policy()
    changed['version'] = 'prototype-v2-test'
    monkeypatch.setattr(workflow, 'load_policy', lambda: deepcopy(changed))
    assert_held_without_delivery(client, run)

def test_invalid_edit_can_be_corrected_and_approved_as_new_revision(client):
    run = approved(client)
    original = run['revisions'][-1]
    changed = client.edit(original['id'], subject=original['subject'], body=original['body'] + '\nPay INR 999.')
    assert changed['state'] == 'validation_failed'
    corrected = client.edit(changed['latest_revision_id'], subject=original['subject'], body=original['body'])
    assert corrected['state'] == 'drafted'
    assert len(corrected['revisions']) == 3
    reviewed = client.review(corrected['latest_revision_id'], action='approve')
    delivered = client.deliver(reviewed['latest_revision_id'])
    assert delivered['state'] == 'delivered'
    assert delivered['deliveries'][0]['message_id'] == corrected['latest_revision_id']

@pytest.mark.skipif(not os.getenv('TEST_POSTGRES_URL'), reason='Set TEST_POSTGRES_URL for isolated real PostgreSQL race checks.')
@pytest.mark.parametrize('same_revision', [False, True], ids=['competing-runs', 'same-revision-retry'])
def test_real_postgres_concurrent_delivery_is_exactly_once(same_revision):
    """Separate temporary schema prevents writes to any demo/application records."""
    url = make_url(os.environ['TEST_POSTGRES_URL'])
    assert url.get_backend_name() == 'postgresql'
    schema = 'acceptance_' + uuid4().hex
    admin = make_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({'options': f'-csearch_path={schema}'})
    engine = make_engine(isolated)
    try:
        initialize_database(engine)
        with make_session_factory(engine)() as session:
            seed_demo(session)
            session.commit()
        app = EngagementService(Settings(str(isolated)), engine)
        with app as client:
            first = approved(client)
            second = first if same_revision else approved(client)
            barrier = Barrier(2)

            def send(run):
                barrier.wait(timeout=10)
                try:
                    return client.deliver(run['latest_revision_id'])
                except ServiceError as error:
                    return error
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(send, run) for run in (first, second)]
                responses = [future.result(timeout=20) for future in pending]
            assert sum((isinstance(result, ServiceError) for result in responses)) == (0 if same_revision else 1)
            assert all((result.code == 'conflict' for result in responses if isinstance(result, ServiceError)))
            with app.sessions() as session:
                assert session.scalar(select(func.count()).select_from(DeliveryJob)) == 1
            winner = next((result for result in responses if isinstance(result, dict)))
            assert winner['state'] == 'delivered'
            assert len(client.deliver(winner['latest_revision_id'])['deliveries']) == 1
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
