import json

import pytest

from devrag.distill import Distiller
from scripts import history_knowledge
from scripts.build_knowledge import cache_input
from scripts.history_knowledge import LESSON_SYSTEM, merge_topics, prepare_session, request_json, shard_session, validate_lessons, validate_topics


def record(number, text, role='user', part=0, line=None):
    return {'id': str(number), 'role': role, 'text': text, 'time': '2026-10-09T00:00:00Z',
            'provenance': {'file': 'source.jsonl', 'line': line or number, 'part': part,
                           'record_sha256': str(line or number)}}


def session(records):
    return {'session': {'id': 'session-a', 'project': 'shop', 'association': 'recorded_git_remote',
                        'started': '2026-10-09', 'harness': 'codex'}, 'source_sha256': 'source-hash',
            'records': records}


def test_configuration_continuations_are_not_mistaken_for_project_requests():
    data = session([record(1, '# AGENTS.md instructions\n<INSTRUCTIONS>global ', line=1),
                    record(2, 'rules that mention shop and deploy</INSTRUCTIONS>', part=1, line=1),
                    record(3, 'Repair the checkout total'), record(4, 'Repair the checkout total'),
                    record(5, '<local-command-stdout>Set effort to max</local-command-stdout>')])
    prepared = prepare_session(data)
    assert [item['text'] for item in prepared['requests']] == ['Repair the checkout total']
    assert prepared['requests'][0]['record_ids'] == ['3', '4']
    assert prepared['coverage']['excluded_configuration_records'] == 3
    assert len(data['records']) == 5


def test_a_user_request_after_environment_metadata_is_preserved():
    data = session([record(1, '<environment_context>cwd=shop</environment_context>\nFix the tax rounding')])
    prepared = prepare_session(data)
    assert prepared['requests'][0]['text'] == 'Fix the tax rounding'


def test_assistant_summaries_do_not_become_user_decisions_or_execution_evidence():
    prepared = prepare_session(session([record(1, 'Is the migration finished?'),
                                       record(2, 'All tests passed.', 'assistant')]))
    assert len(prepared['requests']) == 1
    assert prepared['assistant_context'][0]['role'] == 'assistant'
    assert prepared['assistant_context'][0]['evidence_status'] == 'unverified_claim'


def test_long_messages_are_not_silently_truncated():
    prepared = prepare_session(session([record(1, 'start ' + 'x' * 10000 + ' critical decision')]))
    assert prepared['requests'][0]['text'].endswith(' critical decision')


def test_topic_classification_requires_exact_coverage_and_real_record_citations():
    inputs = [prepare_session(session([record(1, 'Repair checkout')]))]
    good = {'sessions': [{'id': 'session-a', 'relevance': 'project', 'topics': ['checkout'],
                          'reason': 'User requested checkout work', 'evidence_ids': ['1']}]}
    assert validate_topics(good, inputs)['sessions'][0]['relevance'] == 'project'
    with pytest.raises(ValueError, match='coverage'):
        validate_topics({'sessions': []}, inputs)
    with pytest.raises(ValueError, match='evidence'):
        validate_topics({'sessions': [{**good['sessions'][0], 'evidence_ids': ['invented']}]}, inputs)


def test_workspace_association_alone_cannot_support_a_project_classification():
    inputs = [prepare_session(session([]))]
    with pytest.raises(ValueError, match='evidence'):
        validate_topics({'sessions': [{'id': 'session-a', 'relevance': 'project', 'topics': ['shop'],
                                      'reason': 'Workspace name', 'evidence_ids': []}]}, inputs)


def test_topic_shards_preserve_the_tail_of_every_user_request():
    prepared = prepare_session(session([record(1, 'first ' * 500), record(2, 'last important decision')]))
    shards = shard_session(prepared, characters=1000)
    assert len(shards) > 1
    restored = ''.join(row['text'] for shard in shards for row in shard['requests'])
    assert restored == 'first ' * 499 + 'firstlast important decision'
    assert len({shard['id'] for shard in shards}) == len(shards)


def test_a_migration_section_is_not_hidden_by_another_project_section():
    results = [
        {'id': 'session-a/1', 'relevance': 'project', 'topics': ['checkout'], 'reason': 'Code work', 'evidence_ids': ['1']},
        {'id': 'session-a/2', 'relevance': 'unrelated', 'topics': ['profile migration'], 'reason': 'Host work', 'evidence_ids': ['2']},
    ]
    merged = merge_topics('session-a', results)
    assert merged['relevance'] == 'mixed'
    assert len(merged['sections']) == 2


def test_lesson_requires_a_real_history_quote_and_a_real_code_quote():
    records = [record(1, 'Keep the tax check before charging.')]
    files = {'checkout.py': 'validate_tax(order)\ncharge(order)\n'}
    result = {'items': [{'title': 'Tax ordering', 'kind': 'user_decision', 'detail': 'Check tax before charge.',
                         'evidence': [{'record_id': '1', 'quote': 'tax check before charging'}],
                         'code': [{'path': 'checkout.py', 'quote': 'validate_tax(order)'}],
                         'scope': 'Historical user requirement; static code only'}], 'gaps': []}
    assert validate_lessons(result, records, files) == result
    result['items'][0]['code'][0]['quote'] = 'invented validation call'
    with pytest.raises(ValueError, match='code quote'):
        validate_lessons(result, records, files)
    result['items'][0]['code'][0]['quote'] = 'validate_tax(order)'
    result['items'][0]['evidence'][0]['quote'] = 'all tests passed'
    with pytest.raises(ValueError, match='history quote'):
        validate_lessons(result, records, files)


def test_assistant_claim_cannot_be_published_as_a_user_decision():
    result = {'items': [{'title': 'Ship now', 'kind': 'user_decision', 'detail': 'Approved.',
                         'evidence': [{'record_id': '1', 'quote': 'I approved deployment'}],
                         'code': [], 'scope': 'History'}], 'gaps': []}
    with pytest.raises(ValueError, match='user evidence'):
        validate_lessons(result, [record(1, 'I approved deployment', 'assistant')], {})


def test_history_only_lesson_keeps_its_missing_source_boundary():
    result = {'items': [{'title': 'Old incident', 'kind': 'candidate', 'detail': 'Possible retry issue.',
                         'evidence': [{'record_id': '1', 'quote': 'connection failed'}],
                         'code': [], 'scope': 'Historical claim; no matching current source'}],
              'gaps': ['Current source could not be linked']}
    assert validate_lessons(result, [record(1, 'connection failed', 'assistant')], {}) == result


def test_multiline_code_quotes_ignore_repomix_display_numbers_and_preserve_source_whitespace():
    result = {'items': [{'title': 'Tax order', 'kind': 'candidate', 'detail': 'Validate before charge.',
                         'evidence': [{'record_id': '1', 'quote': 'tax'}],
                         'code': [{'path': 'checkout.py', 'quote': 'def pay():\n    validate_tax(order)\n    charge(order)'}],
                         'scope': 'Static source'}], 'gaps': []}
    numbered = '  9: def pay():\n 10:     validate_tax(order)\n 11:     charge(order)'
    assert validate_lessons(result, [record(1, 'Check tax')], {'checkout.py': [numbered]}) == result
    with pytest.raises(ValueError, match='code quote'):
        validate_lessons(result, [record(1, 'Check tax')], {'checkout.py': [numbered.replace('    charge', '  charge')]})
    with pytest.raises(ValueError, match='code quote'):
        validate_lessons(result, [record(1, 'Check tax')], {'checkout.py': [numbered.replace(' 11:', ' 12:')]})


def test_rejected_history_result_is_revalidated_without_another_paid_request(tmp_path, monkeypatch):
    model = Distiller()
    data = {'repository': {'name': 'example', 'commit': 'head'}, 'records': [record(1, 'Check tax')],
            'code': {'checkout.py': ['  1: validate_tax(order)\n  2: charge(order)']}}
    _, fingerprint, destination, _ = cache_input(model, data, tmp_path, LESSON_SYSTEM)
    result = {'items': [{'title': 'Tax order', 'kind': 'candidate', 'detail': 'Validate before charge.',
                         'evidence': [{'record_id': '1', 'quote': 'tax'}],
                         'code': [{'path': 'checkout.py', 'quote': 'validate_tax(order)\ncharge(order)'}],
                         'scope': 'Static source'}], 'gaps': []}
    rejected = tmp_path / (fingerprint + '.rejected.json')
    rejected.write_text(json.dumps({'raw': json.dumps(result), 'usage': {'input_tokens': 10, 'output_tokens': 20}}),
                        encoding='utf-8')

    def no_request(*args):
        pytest.fail('A paid request was issued for an already returned valid result')

    monkeypatch.setattr(history_knowledge, 'generate_summary', no_request)
    saved = request_json(model, data, tmp_path, LESSON_SYSTEM,
                         lambda value: validate_lessons(value, data['records'], data['code']))
    assert saved['result'] == result
    assert saved['recovered_from'] == rejected.name
    assert saved['usage']['output_tokens'] == 20
    assert json.loads(destination.read_text(encoding='utf-8'))['result'] == result
