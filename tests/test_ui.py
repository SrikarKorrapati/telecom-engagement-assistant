"""UI boundary tests without a browser, sockets, live model calls, or a database."""
from __future__ import annotations
from datetime import date
from unittest.mock import Mock
import pandas as pd
import pytest
from app.service import EngagementService, ServiceError
from ui import dataset_dashboard as dataset
from ui import streamlit_app as reviewer
from test_critique import exchange  # Scripted AI responses; never calls the network.

def bind_service(monkeypatch, **methods):
    service = Mock(spec=EngagementService)
    for name, value in methods.items():
        if isinstance(value, Mock):
            setattr(service, name, value)
        else:
            getattr(service, name).return_value = value
    monkeypatch.setattr(reviewer, 'get_service', lambda: service)
    return service

class Rerun(Exception):
    pass

class FakeStreamlit:
    """Exercise UI branches and serialize actual charts without Streamlit AppTest."""

    def __init__(self, *, press=None, choices=None):
        self.session_state = {}
        self.sidebar = self
        self.press = press
        self.choices = choices or {}
        self.buttons = {}
        self.outputs = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def columns(self, value):
        return [self] * (value if isinstance(value, int) else len(value))

    def tabs(self, value):
        return [self] * len(value)

    def expander(self, *args, **kwargs):
        return self
    spinner = form = expander

    def button(self, name, *args, **kwargs):
        self.buttons[name] = kwargs
        return name == self.press and (not kwargs.get('disabled', False))
    form_submit_button = download_button = button

    def selectbox(self, name, options, index=0, **kwargs):
        return self.choices.get(name, self.session_state.get(kwargs.get('key'), options[index]))

    def radio(self, name, options, **kwargs):
        return self.choices.get(name, self.session_state.get(kwargs.get('key'), options[0]))

    def checkbox(self, name, value=False, **kwargs):
        return self.choices.get(name, value)

    def text_input(self, name, value='', **kwargs):
        return self.choices.get(name, value)
    text_area = text_input

    def altair_chart(self, chart, **kwargs):
        self.outputs.append(('altair_chart', chart.to_dict(), kwargs))

    def rerun(self):
        raise Rerun()
    stop = rerun

    def __getattr__(self, name):

        def record(*args, **kwargs):
            self.outputs.append((name, args, kwargs))
        return record

@pytest.fixture
def demo():
    revision = {'id': 'm1', 'revision_no': 1, 'subject': 'Subject', 'body': 'Synthetic body', 'provider': 'template', 'content_hash': 'abc', 'validation': {'passed': True, 'checks': [{'code': 'facts', 'passed': True, 'detail': 'Verified'}]}}
    run = {'id': 'r1', 'customer_id': 'c1', 'state': 'drafted', 'created_at': '2026-11-17', 'snapshot': {'customer_version': 1}, 'segments': [{'segment': 'loyalty_candidate', 'status': 'match', 'evidence_quality': 'verified'}], 'strategy': {'objective': 'offer_or_reward'}, 'latest_revision_id': 'm1', 'revisions': [revision]}
    customer = {'id': 'c1', 'name': 'Synthetic customer', 'email': 'demo@example.test', 'scenario': 'loyalty', 'version': 1, 'context': {'plan_code': 'DEMO', 'tenure_months': 18}}
    return {'config': {'delivery_mode': 'mock', 'openai_configured': False}, 'customer': customer, 'summary': {key: value for key, value in customer.items() if key != 'context'}, 'revision': revision, 'run': run, 'dashboard': {'counts': {'customers': 1, 'runs': 1, 'awaiting_review': 1}, 'segments': [{'segment': 'loyalty_candidate', 'count': 1}], 'strategies': [{'objective': 'offer_or_reward', 'count': 1}], 'recent_activity': []}}

def test_configured_critic_blocks_legacy_revision_without_assessment(monkeypatch, demo):
    ui = FakeStreamlit()
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_message(demo['run'], {**demo['config'], 'critique_enabled': True})
    assert ui.buttons['Approve exact revision']['disabled']
    assert not ui.buttons['Run AI critique']['disabled']

@pytest.mark.parametrize('button,method,identifier,payload', [('Run AI critique', 'critique', 'm1', {}), ('Regenerate email', 'draft', 'r1', {'provider': 'openai'})])
def test_critic_controls_are_explicit_mutations(monkeypatch, demo, button, method, identifier, payload):
    ui = FakeStreamlit(press=button)
    request = Mock(return_value=demo['run'])
    bind_service(monkeypatch, **{method: request})
    monkeypatch.setattr(reviewer, 'st', ui)
    with pytest.raises(Rerun):
        reviewer.render_message({**demo['run'], 'state': 'validation_failed'}, {**demo['config'], 'critique_enabled': True})
    request.assert_called_once_with(identifier, **payload)

def test_critic_findings_and_attempt_are_visible_and_block_approval(monkeypatch, demo):
    ui = FakeStreamlit()
    monkeypatch.setattr(reviewer, 'st', ui)
    validation = {'passed': False, 'critique_required': True, 'generation_attempt': 3, 'generation_attempt_limit': 3, 'critique': {'status': 'changes_requested', 'model_version': 'test-model', 'prompt_version': 'telecom-critique-v1', 'summary': 'The opening implies a consequence.', 'findings': [{'category': 'tone', 'severity': 'blocking', 'quote': 'Bad wording', 'explanation': 'Unsupported pressure', 'suggested_fix': 'Use courteous wording'}]}}
    revision = {**demo['revision'], 'validation': validation}
    reviewer.render_message({**demo['run'], 'state': 'validation_failed', 'revisions': [revision]}, {**demo['config'], 'critique_enabled': True})
    assert ui.buttons['Approve exact revision']['disabled']
    assert any(('Unsupported pressure' in str(item) for item in ui.outputs))
    assert any(('attempt 3 of 3' in str(item) for item in ui.outputs))

def test_render_is_read_only_and_summary_customer_is_supported(monkeypatch, demo):
    ui = FakeStreamlit()
    ui.session_state.update(previous_customer_id='c1', active_run_id='r1')
    service = bind_service(monkeypatch, config=demo['config'], list_customers=[demo['summary']], get_customer=demo['customer'], list_runs=[demo['run']], get_run=demo['run'], dashboard=demo['dashboard'], evaluation_summary={'human': {'reviews': 0, 'scored_reviews': 0, 'mean_scores': {}}, 'automated': {'reviews': 0, 'scored_reviews': 0, 'mean_scores': {}}, 'unscored_reviews': 0})
    for name in ('add_customer', 'create_run', 'draft', 'edit', 'critique', 'review', 'deliver', 'simulate'):
        getattr(service, name).side_effect = AssertionError('Rendering must not mutate data')
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.main()
    service.get_customer.assert_called_once_with('c1')
    service.list_runs.assert_called_once_with('c1')
    service.dashboard.assert_not_called()
    assert ui.buttons['Run final checks and mock deliver']['disabled']

@pytest.mark.parametrize('state, passed, approve_disabled, deliver_disabled', [('drafted', True, False, True), ('validation_failed', False, True, True), ('approved', True, True, False)])
def test_review_buttons_obey_state_and_validation(monkeypatch, demo, state, passed, approve_disabled, deliver_disabled):
    ui = FakeStreamlit()
    run = {**demo['run'], 'state': state, 'revisions': [{**demo['revision'], 'validation': {'passed': passed}}]}
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_message(run, demo['config'])
    assert ui.buttons['Approve exact revision']['disabled'] == approve_disabled
    assert ui.buttons['Run final checks and mock deliver']['disabled'] == deliver_disabled

@pytest.mark.parametrize('state', ['held', 'no_outreach', 'rejected'])
def test_closed_evaluations_have_no_review_or_delivery_actions(monkeypatch, demo, state):
    ui = FakeStreamlit()
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_message({**demo['run'], 'state': state}, demo['config'])
    assert 'Approve exact revision' not in ui.buttons
    assert 'Run final checks and mock deliver' not in ui.buttons

@pytest.mark.parametrize('button,state,method', [('Save new revision', 'drafted', 'edit'), ('Approve exact revision', 'drafted', 'review'), ('Reject', 'drafted', 'review'), ('Run final checks and mock deliver', 'approved', 'deliver')])
def test_deliberate_mutations_target_exact_revision_once(monkeypatch, demo, button, state, method):
    ui = FakeStreamlit(press=button)
    request = Mock(return_value=demo['run'])
    service = bind_service(monkeypatch, **{method: request})
    monkeypatch.setattr(reviewer, 'st', ui)
    with pytest.raises(Rerun):
        reviewer.render_message({**demo['run'], 'state': state}, demo['config'])
    assert request.call_count == 1
    assert request.call_args.args == ('m1',)
    assert len(service.mock_calls) == 1

def test_rejected_mutation_refreshes_state_and_preserves_reason(monkeypatch):
    ui = FakeStreamlit()
    monkeypatch.setattr(reviewer, 'st', ui)
    operation = Mock(side_effect=ServiceError('Approval is stale'))
    with pytest.raises(Rerun):
        reviewer.mutate(operation, 'success')
    operation.assert_called_once_with()
    assert ui.session_state['flash'] == ('error', 'Approval is stale')

@pytest.mark.parametrize('deliveries, downloadable', [([], False), ([{'message_id': 'm1', 'status': 'delivered'}], True)])
def test_download_requires_actual_matching_delivery_record(monkeypatch, demo, deliveries, downloadable):
    ui = FakeStreamlit()
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_message({**demo['run'], 'state': 'delivered', 'deliveries': deliveries}, demo['config'])
    assert ('Download approved mock output' in ui.buttons) == downloadable

def test_dashboard_failure_is_recoverable(monkeypatch):
    ui = FakeStreamlit()
    monkeypatch.setattr(dataset, 'st', ui)
    service = Mock(spec=EngagementService)
    service.dashboard.side_effect = ServiceError('Database unavailable', 'unavailable')
    monkeypatch.setattr(dataset, 'get_service', lambda: service)
    dataset.render_overview()
    assert 'Retry dashboard' in ui.buttons

def test_ai_draft_is_the_only_generation_option(monkeypatch, demo):
    ui = FakeStreamlit(press='Generate email')
    ui.selectbox = Mock(side_effect=AssertionError('No provider selector should be shown'))
    request = Mock(return_value=demo['run'])
    bind_service(monkeypatch, draft=request)
    monkeypatch.setattr(reviewer, 'st', ui)
    config = {**demo['config'], 'openai_configured': True, 'openai_model': 'gpt-6-astra', 'openai_reasoning_effort': 'high'}
    run = {**demo['run'], 'state': 'evaluated', 'revisions': [], 'latest_revision_id': None}
    with pytest.raises(Rerun):
        reviewer.render_message(run, config)
    request.assert_called_once_with('r1', provider='openai')
    ui.selectbox.assert_not_called()

def test_unknown_invoice_and_usage_evidence_never_becomes_zero():
    assert dataset.invoice_totals(None, date(2026, 11, 17)) == (None, None)
    assert dataset.invoice_totals([], date(2026, 11, 17)) == (0, 0)
    assert dataset.invoice_totals([{'outstanding': None, 'due_date': '2026-11-01'}], date(2026, 11, 17)) == (None, None)
    assert dataset.active_days({'current_complete': False, 'current_active_days': 0, 'current_days': 30}, 'current') is None
    assert dataset.active_days({'current_complete': True, 'current_active_days': 0, 'current_days': 30}, 'current') == 0

@pytest.fixture(scope='module')
def dataset_content():
    return dataset.load_data()

def test_dataset_load_preserves_unknowns_and_customer_joins(dataset_content):
    manifest, frame, customers, labels, contexts = dataset_content
    assert len(frame) == manifest['count'] == len(customers) == len(labels) == len(contexts)
    assert frame.loc[frame.scenario == 'invoices_missing', 'outstanding_inr'].isna().all()
    assert frame.loc[frame.scenario == 'usage_incomplete', 'current_active_days'].isna().all()
    assert len(dataset.filtered_rows(frame, 'invoices_missing', 'billing_reminder', 'All')) == 0
    assert len(dataset.filtered_rows(frame, 'All', 'All', 'All', frame.iloc[0].customer_id)) == 1

def test_dataset_page_shows_live_overview_and_customer(monkeypatch, dataset_content, demo):
    ui = FakeStreamlit(choices={'Search customers': 'Customer 00003'})
    monkeypatch.setattr(dataset, 'st', ui)
    monkeypatch.setattr(dataset, 'load_data', lambda: dataset_content)
    service = Mock(spec=EngagementService)
    service.dashboard.return_value = demo['dashboard']
    monkeypatch.setattr(dataset, 'get_service', lambda: service)
    dataset.main()
    assert service.mock_calls == [('dashboard', (), {})]
    assert sum(item[0] == 'bar_chart' for item in ui.outputs) == 2
    assert sum(item[0] == 'metric' for item in ui.outputs) == 6
    tables = [item[1][0] for item in ui.outputs if item[0] == 'dataframe']
    assert len(tables) == 1 and len(tables[0]) == 1
    assert list(tables[0].columns) == ['Customer', 'Plan', 'Stored strategy']
    assert tables[0].iloc[0]['Customer'] == 'Customer 00003'
    assert not any(item[0] == 'altair_chart' for item in ui.outputs)
    assert any(item[0] == 'subheader' and 'Customer 00003' in str(item) for item in ui.outputs)


def test_zero_result_dataset_search_is_safe(monkeypatch, dataset_content):
    ui = FakeStreamlit(choices={'Search customers': 'no-such-customer'})
    monkeypatch.setattr(dataset, 'st', ui)
    monkeypatch.setattr(dataset, 'load_data', lambda: dataset_content)
    service = Mock(spec=EngagementService)
    service.dashboard.side_effect = ServiceError('Database unavailable', 'unavailable')
    monkeypatch.setattr(dataset, 'get_service', lambda: service)
    dataset.main()
    assert not any(item[0] == 'dataframe' for item in ui.outputs)
    assert any('No customers match' in str(item) for item in ui.outputs)


def test_customer_details_keep_missing_usage_and_balance_unknown(monkeypatch):
    ui = FakeStreamlit()
    monkeypatch.setattr(dataset, 'st', ui)
    dataset.show_customer({'name': 'Test', 'context': {}}, {}, {})
    assert any('**Recent activity:** Unknown' in str(item) for item in ui.outputs)
    assert any('**Outstanding balance:** Unknown' in str(item) for item in ui.outputs)

@pytest.fixture
def direct_service(monkeypatch, exchange):
    """Exercise actual service transactions through the UI without any HTTP server."""
    from app.config import Settings
    from app.db import initialize_database, make_engine, make_session_factory
    from app.seed import seed_demo
    engine = make_engine('sqlite:///:memory:')
    initialize_database(engine)
    with make_session_factory(engine).begin() as session:
        seed_demo(session)
    try:
        with EngagementService(Settings('sqlite:///:memory:', openai_api_key='TEST-KEY', openai_model='test-model'), engine) as service:
            spy = Mock(spec=EngagementService, wraps=service)
            monkeypatch.setattr(reviewer, 'get_service', lambda: spy)
            yield (service, spy)
    finally:
        engine.dispose()

def click_reviewer(ui, button):
    ui.press = button
    with pytest.raises(Rerun):
        reviewer.main()
    ui.press = None

def test_direct_service_reviewer_ai_edit_approve_deliver_flow(monkeypatch, direct_service):
    from app.seed import demo_id
    client, spy = direct_service
    customer_id = demo_id('customer:loyalty')
    ui = FakeStreamlit(choices={'Workspace': 'Review customer', 'Customer': customer_id})
    monkeypatch.setattr(reviewer, 'st', ui)
    click_reviewer(ui, 'Evaluate customer')
    run_id = ui.session_state['active_run_id']
    assert client.get_run(run_id)['state'] == 'evaluated'
    click_reviewer(ui, 'Generate email')
    draft = client.get_run(run_id)
    assert draft['state'] == 'drafted'
    assert draft['revisions'][0]['provider'] == 'openai'
    old_revision_id = draft['latest_revision_id']
    click_reviewer(ui, 'Approve exact revision')
    assert client.get_run(run_id)['state'] == 'approved'
    ui.choices['Updated body'] = draft['revisions'][0]['body'] + '\nThank you.'
    click_reviewer(ui, 'Save new revision')
    edited = client.get_run(run_id)
    assert edited['latest_revision_id'] != old_revision_id
    assert edited['state'] == 'drafted'
    reviewer.main()
    assert ui.buttons['Run final checks and mock deliver']['disabled'] is True
    click_reviewer(ui, 'Approve exact revision')
    click_reviewer(ui, 'Run final checks and mock deliver')
    delivered = client.get_run(run_id)
    assert delivered['state'] == 'delivered'
    assert len(delivered['deliveries']) == 1
    assert delivered['deliveries'][0]['message_id'] == edited['latest_revision_id']
    reviewer.main()
    assert 'Download approved mock output' in ui.buttons
    spy.list_runs.assert_called_with(customer_id)
    assert all((call.args == (customer_id,) for call in spy.list_runs.call_args_list))
    assert sum((getattr(spy, name).call_count for name in ('create_run', 'draft', 'review', 'edit', 'deliver'))) == 6
    assert client.config()['openai_configured'] is True

@pytest.mark.parametrize('customer_key,event', [('billing', 'payment'), ('loyalty', 'opt_out'), ('billing', 'dispute')])
def test_direct_service_synthetic_changes_block_stale_approved_delivery(monkeypatch, direct_service, customer_key, event):
    from app.seed import demo_id
    client, _ = direct_service
    customer_id = demo_id('customer:' + customer_key)
    ui = FakeStreamlit(choices={'Workspace': 'Review customer', 'Synthetic customer': customer_id, 'Event': event})
    monkeypatch.setattr(reviewer, 'st', ui)
    for button in ('Evaluate customer', 'Generate email', 'Approve exact revision', 'Apply synthetic event'):
        click_reviewer(ui, button)
    run_id = ui.session_state['active_run_id']
    assert client.get_run(run_id)['state'] == 'approved'
    click_reviewer(ui, 'Run final checks and mock deliver')
    blocked = client.get_run(run_id)
    assert blocked['state'] == 'held'
    assert blocked['blocked_reasons']
    assert not any((item['status'] == 'delivered' for item in blocked['deliveries']))
    assert ui.session_state['flash'][0] == 'error'
    ui.buttons.clear()
    reviewer.main()
    assert 'Run final checks and mock deliver' not in ui.buttons

@pytest.mark.parametrize('action', ['Approve exact revision', 'Reject'])
@pytest.mark.parametrize('scored', [False, True])
def test_human_review_scores_are_optional_and_explicit(monkeypatch, demo, action, scored):
    chosen = {'Score message usefulness': scored, 'Relevance': 5, 'Clarity': 4, 'Personalization': 3, 'Call to action': 2, 'Tone': 1}
    ui = FakeStreamlit(press=action, choices=chosen)
    request = Mock(return_value=demo['run'])
    monkeypatch.setattr(reviewer, 'st', ui)
    bind_service(monkeypatch, review=request)
    with pytest.raises(Rerun):
        reviewer.render_message(demo['run'], demo['config'])
    payload = request.call_args.kwargs
    assert payload['review_source'] == 'human'
    assert payload['scores'] == ({'relevance': 5, 'clarity': 4, 'personalization': 3, 'call_to_action': 2, 'tone': 1} if scored else {})

def test_edits_do_not_submit_review_scores(monkeypatch, demo):
    ui = FakeStreamlit(press='Save new revision', choices={'Score message usefulness': True})
    request = Mock(return_value=demo['run'])
    monkeypatch.setattr(reviewer, 'st', ui)
    bind_service(monkeypatch, edit=request)
    with pytest.raises(Rerun):
        reviewer.render_message(demo['run'], demo['config'])
    assert 'scores' not in request.call_args.kwargs
    assert 'review_source' not in request.call_args.kwargs

@pytest.mark.parametrize('value', ['[]', 'null', '{"active": NaN}', '{bad json}'])
def test_profile_context_rejects_non_objects_and_invalid_json(value):
    with pytest.raises(ValueError):
        reviewer.parse_profile_context(value)
    assert reviewer.parse_profile_context('{"invoices": null, "tenure_months": 12}') == {'invoices': None, 'tenure_months': 12}

def test_add_profile_save_and_evaluation_are_separate_explicit_actions(monkeypatch):
    example = {'name': 'Synthetic Person', 'email': 'synthetic.profile@example.com', 'scenario': 'custom_synthetic', 'context': {'synthetic': True, 'invoices': None}}
    ui = FakeStreamlit(press='Save synthetic profile')
    service = bind_service(monkeypatch, profile_example=example, add_customer={**example, 'id': 'new-profile'}, create_run={'id': 'new-run', 'customer_id': 'new-profile'})
    monkeypatch.setattr(reviewer, 'st', ui)
    with pytest.raises(Rerun):
        reviewer.render_add_profile()
    service.add_customer.assert_called_once_with({**example, 'name': 'Person'})
    service.create_run.assert_not_called()
    assert ui.session_state['created_profile']['id'] == 'new-profile'
    assert 'active_run_id' not in ui.session_state
    ui.press = 'Evaluate this profile'
    with pytest.raises(Rerun):
        reviewer.render_add_profile()
    service.create_run.assert_called_once_with('new-profile')
    assert ui.session_state['pending_navigation'] == {'customer_id': 'new-profile', 'run_id': 'new-run'}
    assert len(service.mock_calls) == 3

def test_add_profile_invalid_json_never_calls_service_save(monkeypatch):
    example = {'name': 'Synthetic Person', 'email': 'synthetic.profile@example.com', 'context': {'synthetic': True}}
    ui = FakeStreamlit(press='Save synthetic profile', choices={'Customer context (JSON)': '[null]'})
    service = bind_service(monkeypatch, profile_example=example)
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_add_profile()
    service.profile_example.assert_called_once_with()
    service.add_customer.assert_not_called()
    assert any(('JSON object' in str(item) for item in ui.outputs))

def test_review_history_separates_human_automated_and_unspecified(monkeypatch, demo):
    ui = FakeStreamlit()
    reviews = [{'message_id': 'm1', 'action': 'approve', 'review_source': source, 'scores': {'relevance': 4}, 'feedback': f'{source} feedback'} for source in ['human', 'automated', 'unspecified']]
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_message({**demo['run'], 'reviews': reviews}, demo['config'])
    for title in ['Human reviews', 'Automated reviews', 'Reviews with no recorded source']:
        assert any((title in str(item) for item in ui.outputs))

def test_direct_service_profile_create_evaluate_and_open_customer(monkeypatch, direct_service):
    client, spy = direct_service
    ui = FakeStreamlit(choices={'Workspace': 'Add profile'})
    monkeypatch.setattr(reviewer, 'st', ui)
    click_reviewer(ui, 'Save synthetic profile')
    customer_id = ui.session_state['created_profile']['id']
    created = client.get_customer(customer_id)
    assert created['context']['synthetic'] is True
    assert created['email'].endswith('@example.com')
    spy.create_run.assert_not_called()
    click_reviewer(ui, 'Evaluate this profile')
    run_id = ui.session_state['pending_navigation']['run_id']
    ui.choices.pop('Workspace')
    reviewer.main()
    assert ui.session_state['workspace_page'] == 'Review customer'
    assert ui.session_state['customer_picker'] == customer_id
    assert ui.session_state['active_run_id'] == run_id
    assert client.get_run(run_id)['customer_id'] == customer_id

def test_direct_service_profile_unknown_evidence_is_preserved_and_held(monkeypatch, direct_service):
    import json
    client, _ = direct_service
    example = client.profile_example()
    context = example['context']
    context['invoices'] = None
    ui = FakeStreamlit(choices={'Workspace': 'Add profile', 'Customer context (JSON)': json.dumps(context)})
    monkeypatch.setattr(reviewer, 'st', ui)
    click_reviewer(ui, 'Save synthetic profile')
    customer_id = ui.session_state['created_profile']['id']
    assert client.get_customer(customer_id)['context']['invoices'] is None
    click_reviewer(ui, 'Evaluate this profile')
    run_id = ui.session_state['pending_navigation']['run_id']
    assert client.get_run(run_id)['state'] == 'held'

@pytest.mark.parametrize('field,value', [('age', 45), ('tenure_months', '12'), ('synthetic', False)])
def test_direct_service_profile_invalid_context_has_actionable_field_error(monkeypatch, direct_service, field, value):
    import json
    client, _ = direct_service
    example = client.profile_example()
    example['context'][field] = value
    ui = FakeStreamlit(press='Save synthetic profile', choices={'Customer context (JSON)': json.dumps(example['context'])})
    monkeypatch.setattr(reviewer, 'st', ui)
    reviewer.render_add_profile()
    assert 'created_profile' not in ui.session_state
    assert any((field in str(item) and item[0] == 'error' for item in ui.outputs))

def test_direct_service_human_rubric_is_saved_and_separate_from_automated(monkeypatch, direct_service):
    from app.seed import demo_id
    client, _ = direct_service
    choices = {'Workspace': 'Review customer', 'Synthetic customer': demo_id('customer:loyalty'), 'Score message usefulness': True}
    choices.update({name: 4 for name in reviewer.RUBRIC.values()})
    ui = FakeStreamlit(choices=choices)
    monkeypatch.setattr(reviewer, 'st', ui)
    for button in ('Evaluate customer', 'Generate email', 'Approve exact revision'):
        click_reviewer(ui, button)
    run_id = ui.session_state['active_run_id']
    run = client.get_run(run_id)
    saved = run['reviews'][-1]
    assert saved['review_source'] == 'human'
    assert saved['scores'] == {key: 4 for key in reviewer.RUBRIC}
    client.review(run['latest_revision_id'], action='approve', feedback='Automated integration fixture', scores={key: 2 for key in reviewer.RUBRIC}, review_source='automated')
    summary = client.evaluation_summary()
    assert summary['human']['scored_reviews'] == 1
    assert summary['human']['mean_scores'] == {key: 4.0 for key in reviewer.RUBRIC}
    assert summary['automated']['scored_reviews'] == 1
    assert summary['automated']['mean_scores'] == {key: 2.0 for key in reviewer.RUBRIC}
