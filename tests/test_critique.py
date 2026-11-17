"""Generator–critic integration with scripted model responses, never live requests."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from app.config import Settings
from app.critique import CritiqueResult
from app.db import initialize_database, make_engine, make_session_factory
from app.service import EngagementService, ServiceError
from app.messages import CourtesyCopy
from app.models import MessageRevision
from app.seed import demo_id, seed_demo
PASS = {'verdict': 'pass', 'summary': 'The message is grounded, clear and respectful.', 'findings': []}
REVISE = {'verdict': 'revise', 'summary': 'The opening implies an unsupported service consequence.', 'findings': [{'category': 'policy', 'severity': 'blocking', 'quote': 'You may regret overlooking this.', 'explanation': 'This implies an unsupported consequence and pressure.', 'suggested_fix': 'Use a courteous invitation to review the bill without consequences.'}]}
COPY = {'opening': 'Please take a moment to read this message.', 'closing': 'We are here to help if you need support.'}

@pytest.fixture
def exchange(monkeypatch):
    import openai
    state = SimpleNamespace(calls=[], generations=[], critiques=[])

    class Client:

        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0
            self.responses = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def parse(self, **kwargs):
            state.calls.append(kwargs)
            is_generator = kwargs['text_format'] is CourtesyCopy
            assert is_generator or kwargs['text_format'] is CritiqueResult
            queue = state.generations if is_generator else state.critiques
            value = queue.pop(0) if queue else deepcopy(COPY if is_generator else PASS)
            if isinstance(value, Exception):
                raise value
            if isinstance(value, SimpleNamespace):
                return value
            return SimpleNamespace(status='completed', output=[], model='test-resolved-model', output_parsed=value)
    monkeypatch.setattr(openai, 'OpenAI', Client)
    return state

@pytest.fixture
def client(exchange):
    engine = make_engine('sqlite://')
    initialize_database(engine)
    with make_session_factory(engine).begin() as session:
        seed_demo(session)
    settings = Settings('sqlite://', openai_api_key='TEST-KEY', openai_model='test-model')
    with EngagementService(settings, engine) as client:
        yield client
    engine.dispose()

def evaluate(client, scenario='billing'):
    return client.create_run(demo_id('customer:' + scenario))

def generate(client, provider='openai', scenario='billing'):
    run = evaluate(client, scenario)
    return client.draft(run['id'], provider=provider)

def roles(exchange):
    return ['generator' if request['text_format'] is CourtesyCopy else 'critic' for request in exchange.calls]

def test_generator_critic_loop_repairs_and_preserves_every_attempt(client, exchange):
    exchange.generations = [{'opening': 'You may regret overlooking this.', 'closing': COPY['closing']}, COPY]
    exchange.critiques = [REVISE, PASS]
    run = generate(client)
    assert roles(exchange) == ['generator', 'critic', 'generator', 'critic']
    assert run['state'] == 'drafted'
    first, second = run['revisions']
    assert first['validation']['critique']['status'] == 'changes_requested'
    assert not first['validation']['passed']
    assert second['validation']['passed']
    assert second['validation']['generation_attempt'] == 2
    feedback = json.loads(exchange.calls[2]['input'][1]['content'])['revision_feedback']
    assert feedback['previous_courtesy_copy']['opening'] == 'You may regret overlooking this.'
    assert feedback['findings'][0]['suggested_fix'] == REVISE['findings'][0]['suggested_fix']
    assert first['content_hash'] != second['content_hash']
    assert not run['reviews'] and (not run['deliveries'])
    with pytest.raises(ServiceError) as caught:
        client.deliver(second['id'])
    assert caught.value.code == 'conflict'
    with pytest.raises(ServiceError) as caught:
        client.review(first['id'], action='approve')
    assert caught.value.code == 'conflict'
    client.review(second['id'], action='approve')
    delivered = client.deliver(second['id'])
    assert delivered['state'] == 'delivered'
    assert len(exchange.calls) == 4

def test_loop_stops_after_three_failed_critiques_and_blocks_approval(client, exchange):
    exchange.critiques = [REVISE] * 3
    run = generate(client)
    assert roles(exchange) == ['generator', 'critic'] * 3
    assert run['state'] == 'validation_failed' and len(run['revisions']) == 3
    latest = run['latest_revision_id']
    with pytest.raises(ServiceError) as caught:
        client.review(latest, action='approve')
    assert caught.value.code == 'conflict'
    saved = client.get_run(run['id'])
    assert saved['revisions'][-1]['validation']['critique']['status'] == 'changes_requested'
    assert any((e['event_type'] == 'generation_loop_completed' and e['details']['limit_reached'] for e in saved['audit']))
    repaired = client.draft(run['id'], provider='openai')
    assert repaired['state'] == 'drafted' and len(repaired['revisions']) == 4
    assert 'revision_feedback' in json.loads(exchange.calls[-2]['input'][1]['content'])

@pytest.mark.parametrize('scenario', ['billing', 'loyalty', 'appreciation'])
def test_template_still_receives_critic_when_configured_and_sends_minimal_facts(client, exchange, scenario):
    run = generate(client, 'template', scenario)
    assert roles(exchange) == ['critic']
    request = exchange.calls[0]
    assert request['store'] is False
    assert request['reasoning'] == {'effort': 'high'}
    payload = json.loads(request['input'][1]['content'])
    assert set(payload) == {'purpose', 'tone', 'verified_message', 'draft'}
    encoded = json.dumps(payload)
    for secret in (run['snapshot']['customer_name'], run['snapshot']['customer_email'], run['snapshot']['context']['account_id'], run['snapshot']['context']['subscription_id']):
        assert secret not in encoded
    assert '[customer]' in encoded
    assert 'feedback' not in payload and 'offers' not in payload
    assert run['revisions'][-1]['validation']['critique']['model_version'] == 'test-resolved-model'

def test_rule_failure_is_repaired_without_sending_failed_text_to_critic(client, exchange):
    exchange.generations = [{'opening': 'You get unlimited data.', 'closing': COPY['closing']}, COPY]
    run = generate(client)
    assert roles(exchange) == ['generator', 'generator', 'critic']
    assert run['revisions'][0]['validation']['critique']['status'] == 'skipped'
    feedback = json.loads(exchange.calls[1]['input'][1]['content'])['revision_feedback']
    assert feedback['rule_failures']
    assert 'unlimited' not in json.dumps(feedback)
    assert run['state'] == 'drafted'

@pytest.mark.parametrize('outcome', [RuntimeError('secret TEST-KEY request'), SimpleNamespace(status='incomplete', output=[], output_parsed=PASS), SimpleNamespace(status='completed', output=[SimpleNamespace(content=[SimpleNamespace(type='refusal')])], output_parsed=PASS), {'verdict': 'pass', 'summary': 'Bad result', 'findings': REVISE['findings']}, {'verdict': 'revise', 'summary': 'No specific blocker', 'findings': []}, {'verdict': 'pass', 'summary': 'Unexpected shape', 'findings': [], 'extra': True}])
def test_critic_errors_do_not_fallback_or_loop_and_can_be_retried(client, exchange, outcome):
    exchange.critiques = [outcome]
    run = generate(client)
    assert roles(exchange) == ['generator', 'critic']
    assert run['state'] == 'validation_failed' and len(run['revisions']) == 1
    latest = run['latest_revision_id']
    assert run['revisions'][0]['validation']['critique']['status'] == 'error'
    assert 'TEST-KEY' not in json.dumps(run)
    with pytest.raises(ServiceError) as caught:
        client.review(latest, action='approve')
    assert caught.value.code == 'conflict'
    retried = client.critique(latest)
    assert retried['state'] == 'drafted' and len(retried['revisions']) == 1
    assert roles(exchange) == ['generator', 'critic', 'critic']

def test_human_edit_gets_fresh_critique_without_automatic_rewriting(client, exchange):
    run = generate(client)
    original = run['revisions'][-1]
    client.review(original['id'], action='approve')
    exchange.critiques = [REVISE]
    body = original['body'] + '\nYou may regret overlooking this.'
    edited = client.edit(original['id'], subject=original['subject'], body=body)
    latest = edited['revisions'][-1]
    assert latest['body'] == body and latest['provider'] == 'human_edit'
    assert len(edited['revisions']) == 2 and roles(exchange) == ['generator', 'critic', 'critic']
    assert edited['state'] == 'validation_failed' and latest['validation']['critique_required']
    assert latest['validation']['critique']['input_hash'] != original['validation']['critique']['input_hash']
    with pytest.raises(ServiceError) as caught:
        client.review(latest['id'], action='approve')
    assert caught.value.code == 'conflict'

def test_recritique_invalidates_approval_even_without_text_change(client, exchange):
    run = generate(client)
    latest = run['latest_revision_id']
    client.review(latest, action='approve')
    assert client.critique(latest)['state'] == 'drafted'
    with pytest.raises(ServiceError) as caught:
        client.deliver(latest)
    assert caught.value.code == 'conflict'

@pytest.mark.parametrize('mutation', ['remove', 'hash', 'verdict', 'policy', 'input'])
def test_missing_or_mismatched_critique_cannot_authorize_delivery(client, exchange, mutation):
    run = generate(client)
    latest = run['latest_revision_id']
    client.review(latest, action='approve')
    with client.sessions.begin() as session:
        revision = session.get(MessageRevision, latest)
        validation = deepcopy(revision.validation)
        if mutation == 'remove':
            validation.pop('critique')
        else:
            field = {'hash': 'content_hash', 'verdict': 'verdict', 'policy': 'policy_hash', 'input': 'input_hash'}[mutation]
            validation['critique'][field] = 'wrong'
        revision.validation = validation
    with pytest.raises(ServiceError) as response:
        client.deliver(latest)
    assert response.value.code == 'conflict'
    saved = client.get_run(run['id'])
    assert saved['state'] == 'held' and (not saved['deliveries'])

def test_generator_failure_mid_loop_preserves_failed_attempt_and_audit(client, exchange):
    run = evaluate(client)
    exchange.generations = [COPY, RuntimeError('secret TEST-KEY')]
    exchange.critiques = [REVISE]
    with pytest.raises(ServiceError) as response:
        client.draft(run['id'], provider='openai')
    assert response.value.code == 'invalid_input' and 'TEST-KEY' not in str(response.value)
    saved = client.get_run(run['id'])
    assert saved['state'] == 'validation_failed' and len(saved['revisions']) == 1
    assert any((e['event_type'] == 'generation_failed' and e['details']['attempt'] == 2 for e in saved['audit']))
