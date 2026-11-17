"""Submission profile, rubric, frozen-policy and migration acceptance tests."""
from copy import deepcopy
import os
from uuid import uuid4
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from app.config import Settings
from app.db import make_engine, make_session_factory, initialize_database
from app.service import EngagementService, ServiceError
from app.models import EngagementRun, Review
from app.rubric import CRITERIA
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

def draft(client, scenario='billing'):
    run = client.create_run(demo_id('customer:' + scenario))
    return client.draft(run['id'], provider='template')

def scores(value):
    return {criterion: value for criterion in CRITERIA}

def test_profile_example_is_accepted_and_can_be_evaluated(client):
    profile = client.profile_example()
    assert profile['context']['synthetic'] is True
    customer = client.add_customer(profile)
    run = client.create_run(customer['id'])
    assert len(run['segments']) == 5
    assert run['state'] == 'evaluated'
    assert run['snapshot']['content_policy'] == run['policy_snapshot']['content']

def test_known_missing_profile_evidence_is_preserved_and_held(client):
    customer = client.add_customer({'name': 'Synthetic Missing', 'email': 'missing@example.test', 'context': {'synthetic': True}})
    assert customer['context']['source_as_of'] is None
    assert customer['context']['invoices'] is None
    assert customer['context']['usage']['current_active_days'] is None
    run = client.create_run(customer['id'])
    assert run['state'] == 'held'
    assert all((segment['status'] == 'insufficient_evidence' for segment in run['segments']))
    assert run['blocked_reasons']

@pytest.mark.parametrize('value', [False, 1, 1.0, 0, 'true', None])
def test_profile_requires_literal_boolean_synthetic_true(client, value):
    profile = client.profile_example()
    profile['context']['synthetic'] = value
    with pytest.raises(ServiceError) as caught:
        client.add_customer(profile)
    assert caught.value.code == 'invalid_input'

@pytest.mark.parametrize('email', ['person@gmail.com', 'person@telecom.in', 'person@example.com.evil.org', 'malformed'])
def test_profiles_reject_nonreserved_email_addresses(client, email):
    profile = client.profile_example()
    profile['email'] = email
    with pytest.raises(ServiceError) as caught:
        client.add_customer(profile)
    assert caught.value.code == 'invalid_input'

@pytest.mark.parametrize('field', ['gender', 'ethnicity', 'religion', 'address', 'postcode'])
def test_profile_rejects_protected_and_proxy_fields(client, field):
    profile = client.profile_example()
    profile['context'][field] = 'not a decision input'
    with pytest.raises(ServiceError) as caught:
        client.add_customer(profile)
    assert caught.value.code == 'invalid_input'
    profile['context'].pop(field)
    profile[field] = 'not a profile field'
    with pytest.raises(ServiceError) as caught:
        client.add_customer(profile)
    assert caught.value.code == 'invalid_input'

@pytest.mark.parametrize('path,value', [(('active',), 'true'), (('tenure_months',), '12'), (('usage', 'current_active_days'), True), (('usage', 'current_complete'), 1), (('source_as_of',), 'not-a-timestamp'), (('source_as_of',), '2026-11-17T06:30:00'), (('invoices',), 'unknown')])
def test_malformed_evidence_types_are_rejected_before_storage(client, path, value):
    profile = client.profile_example()
    target = profile['context']
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ServiceError) as caught:
        client.add_customer(profile)
    assert caught.value.code == 'invalid_input'

def test_numeric_money_is_rejected_and_null_money_is_unknown(client):
    profile = client.profile_example()
    profile['context']['invoices'][0]['outstanding'] = 0.0
    with pytest.raises(ServiceError) as caught:
        client.add_customer(profile)
    assert caught.value.code == 'invalid_input'
    profile['context']['invoices'][0]['outstanding'] = None
    customer = client.add_customer(profile)
    run = client.create_run(customer['id'])
    assert run['state'] == 'held'

@pytest.mark.parametrize('value', [True, False, '4', 4.0, 0, 6, None])
def test_rubric_scores_require_strict_integers_in_range(client, value):
    run = draft(client)
    invalid = scores(4)
    invalid['clarity'] = value
    with pytest.raises(ServiceError) as caught:
        client.review(run['latest_revision_id'], action='approve', scores=invalid, review_source='human')
    assert caught.value.code == 'invalid_input'
    saved = client.get_run(run['id'])
    assert saved['state'] == 'drafted'
    assert saved['reviews'] == []

@pytest.mark.parametrize('invalid', [{'clarity': 4}, {**{key: 4 for key in CRITERIA}, 'extra': 4}, [], None])
def test_rubric_requires_all_five_criteria_or_explicitly_unscored(client, invalid):
    run = draft(client)
    with pytest.raises(ServiceError) as caught:
        client.review(run['latest_revision_id'], action='approve', scores=invalid)
    assert caught.value.code == 'invalid_input'

def test_review_scores_bind_to_revision_and_summary_deduplicates_by_source(client):
    run = draft(client)
    revision_id = run['latest_revision_id']
    for value in (4, 3):
        run = client.review(revision_id, action='approve', scores=scores(value), review_source='human', feedback='Human rubric judgment')
    run = client.review(revision_id, action='approve', scores=scores(5), review_source='automated', feedback='Synthetic automated test score')
    assert all((review['message_id'] == revision_id for review in run['reviews']))
    assert all((review['content_hash'] == run['revisions'][-1]['content_hash'] for review in run['reviews']))
    summary = client.evaluation_summary()
    assert summary['human']['reviews'] == summary['human']['scored_reviews'] == 1
    assert summary['human']['mean_scores'] == scores(3)
    assert summary['automated']['scored_reviews'] == 1
    assert summary['automated']['mean_scores'] == scores(5)
    revision = run['revisions'][-1]
    edited = client.edit(revision_id, subject=revision['subject'], body=revision['body'])
    assert edited['state'] == 'drafted'
    assert edited['latest_revision_id'] != revision_id
    assert not any((review['message_id'] == edited['latest_revision_id'] for review in edited['reviews']))
    with pytest.raises(ServiceError) as caught:
        client.deliver(edited['latest_revision_id'])
    assert caught.value.code == 'conflict'

def test_unscored_and_unspecified_reviews_are_not_human_measurements(client):
    run = draft(client)
    client.review(run['latest_revision_id'], action='approve')
    summary = client.evaluation_summary()
    assert summary['unspecified']['reviews'] == 1
    assert summary['human']['scored_reviews'] == summary['automated']['scored_reviews'] == 0
    assert summary['unscored_reviews'] == 1
    assert set(client.rubric()['criteria']) == set(CRITERIA)

def test_content_is_frozen_in_draft_and_changed_configuration_blocks_delivery(client, monkeypatch):
    run = client.create_run(demo_id('customer:loyalty'))
    original = deepcopy(run['snapshot']['content_policy'])
    run = client.draft(run['id'], provider='template')
    assert run['revisions'][-1]['subject'] == original['templates']['offer_or_reward']['subject']
    assert run['revisions'][-1]['template_version'] == original['version']
    run = client.review(run['latest_revision_id'], action='approve')
    changed = workflow.load_policy()
    changed['content']['version'] = 'updated-content-test'
    changed['content']['templates']['offer_or_reward']['subject'] = 'A newly configured offer subject'
    monkeypatch.setattr(workflow, 'load_policy', lambda: deepcopy(changed))
    with pytest.raises(ServiceError) as caught:
        client.deliver(run['latest_revision_id'])
    assert caught.value.code == 'conflict'
    saved = client.get_run(run['id'])
    assert saved['state'] == 'held'
    assert any(('Policy changed' in reason for reason in saved['blocked_reasons']))
    assert saved['deliveries'] == []
    fresh = client.create_run(demo_id('customer:loyalty'))
    assert fresh['snapshot']['content_policy']['version'] == 'updated-content-test'

def test_changed_configuration_blocks_before_any_model_request(client, monkeypatch):
    run = client.create_run(demo_id('customer:loyalty'))
    changed = workflow.load_policy()
    changed['content']['version'] = 'updated-before-draft-test'
    monkeypatch.setattr(workflow, 'load_policy', lambda: deepcopy(changed))

    def must_not_generate(*args, **kwargs):
        raise AssertionError('Configuration preflight must prevent a model request')
    monkeypatch.setattr(workflow, 'generate_message', must_not_generate)
    with pytest.raises(ServiceError) as response:
        client.draft(run['id'], provider='openai')
    assert response.value.code == 'conflict'
    saved = client.get_run(run['id'])
    assert saved['revisions'] == []
    assert saved['state'] == 'held'

def test_legacy_run_without_content_snapshot_remains_readable_but_cannot_deliver(client):
    run = draft(client)
    run = client.review(run['latest_revision_id'], action='approve')
    with client.sessions() as session:
        stored = session.get(EngagementRun, run['id'])
        legacy_policy, legacy_snapshot = (deepcopy(stored.policy_snapshot), deepcopy(stored.snapshot))
        legacy_policy.pop('content')
        legacy_snapshot.pop('content_policy')
        stored.policy_snapshot, stored.snapshot = (legacy_policy, legacy_snapshot)
        stored.policy_hash = workflow.digest(legacy_policy)
        session.commit()
    assert client.get_run(run['id'])
    with pytest.raises(ServiceError) as caught:
        client.deliver(run['latest_revision_id'])
    assert caught.value.code == 'conflict'
    assert client.get_run(run['id'])['deliveries'] == []

def check_legacy_review_migration(engine):
    initialize_database(engine)
    with make_session_factory(engine)() as session:
        seed_demo(session)
        session.commit()
    with EngagementService(Settings('sqlite://'), engine) as client:
        run = draft(client)
        run = client.review(run['latest_revision_id'], action='approve', feedback='Preserve this legacy judgment')
        original = run['reviews'][-1]
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE reviews DROP COLUMN scores'))
        connection.execute(text('ALTER TABLE reviews DROP COLUMN review_source'))
    initialize_database(engine)
    initialize_database(engine)
    assert len(inspect(engine).get_table_names()) == 7
    with make_session_factory(engine)() as session:
        preserved = session.get(Review, original['id'])
        assert preserved.feedback == original['feedback']
        assert preserved.content_hash == original['content_hash']
        assert preserved.message_id == original['message_id']
        assert preserved.scores == {}
        assert preserved.review_source == 'unspecified'
        assert len(session.scalars(select(Review)).all()) == 1

def test_legacy_sqlite_review_migration_is_idempotent_and_preserves_records():
    engine = make_engine('sqlite://')
    try:
        check_legacy_review_migration(engine)
    finally:
        engine.dispose()

@pytest.mark.skipif(not os.getenv('TEST_POSTGRES_URL'), reason='Set TEST_POSTGRES_URL for isolated PostgreSQL migration verification.')
def test_legacy_postgres_review_migration_preserves_records_in_isolated_schema():
    url = make_url(os.environ['TEST_POSTGRES_URL'])
    schema = 'submission_migration_' + uuid4().hex
    admin = make_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = make_engine(url.update_query_dict({'options': f'-csearch_path={schema}'}))
    try:
        check_legacy_review_migration(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
