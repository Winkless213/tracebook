import json
import threading

import pytest

from devrag import distill as distill_module
from scripts import classify_project_history as topics
from scripts import distill_project_history as lessons
from scripts import history_knowledge
from scripts import run_developer_knowledge as runner


def prepare_history(tmp_path, monkeypatch, stage, workers=3):
    repo = {'name': 'example', 'url': 'https://github.com/test/example', 'commit': 'head'}
    pack = tmp_path / 'packs/example/head'
    pack.mkdir(parents=True)
    (pack / 'code.json').write_text(json.dumps({'files': {'calc.py': 'def total():\n    return 1\n'}}), encoding='utf-8')
    target = tmp_path / 'knowledge/example'
    history = target / 'history'
    history.mkdir(parents=True)
    prepared, classifications = [], {}
    for number in range(1, 4):
        identifier = f'session-{number}'
        source = {'session': {'id': identifier, 'harness': 'codex', 'association': 'recorded_git_remote', 'started': '2026-10-10'},
                  'source_sha256': f'hash-{number}', 'records': [
                      {'id': str(number), 'role': 'user', 'text': f'Keep rule {number}.', 'time': '2026-10-10',
                       'provenance': {'file': 'source.jsonl', 'line': number, 'part': 0}}]}
        (history / f'{identifier}.json').write_text(json.dumps(source), encoding='utf-8')
        prepared.append(history_knowledge.prepare_session(source))
        classifications[identifier] = [{'id': identifier + '/1', 'relevance': 'project', 'topics': [f'rule-{number}'],
                                        'reason': 'Explicit project rule', 'evidence_ids': [str(number)]}]
    original_topic_batches, original_lesson_batches = topics.topic_batches, lessons.batches
    monkeypatch.setattr(topics, 'topic_batches', lambda rows: original_topic_batches(rows, characters=1))
    monkeypatch.setattr(lessons, 'batches', lambda rows: original_lesson_batches(rows, characters=1))
    if stage == 'history':
        topics.save_topics(repo, prepared, classifications, [], target)
    action = topics.classify if stage == 'topics' else lessons.distill
    return target, lambda: action(repo, tmp_path / 'packs', tmp_path / 'knowledge', one_batch=True, batch_workers=workers)


def response_for(stage, data):
    if stage == 'topics':
        source = data['sessions'][0]
        number = source['requests'][0]['record_ids'][0]
        result = {'sessions': [{'id': source['id'], 'relevance': 'project', 'topics': ['rule-' + number],
                                'reason': 'Explicit project rule', 'evidence_ids': [number]}]}
    else:
        source = data['records'][0]
        number = source['id']
        result = {'items': [{'title': 'rule-' + number, 'kind': 'user_decision', 'detail': 'Keep the stated project rule.',
                             'scope': 'Historical request; verify current code.',
                             'evidence': [{'record_id': number, 'quote': source['text']}], 'code': []}], 'gaps': []}
    return number, result


@pytest.mark.parametrize('stage', ['topics', 'history'])
def test_history_batches_overlap_and_keep_source_order_with_separate_models(tmp_path, monkeypatch, stage):
    target, run = prepare_history(tmp_path, monkeypatch, stage)
    barrier, second_finished, models = threading.Barrier(3, timeout=5), threading.Event(), []

    def generate(model, data, cache, pending, retry_transient, system):
        number, result = response_for(stage, data)
        models.append(model)
        assert model.yield_between_batches
        barrier.wait()
        if number == '1':
            assert second_finished.wait(5)
        if number == '2':
            second_finished.set()
        return json.dumps(result), {'input_tokens': 1, 'output_tokens': 1}, 1, [], {}

    monkeypatch.setattr(history_knowledge, 'generate_summary', generate)
    report = run()
    assert len({id(model) for model in models}) == 3
    if stage == 'topics':
        assert report['all_sessions_classified']
        assert [row['id'] for row in report['sessions']] == ['session-1', 'session-2', 'session-3']
    else:
        assert report['all_selected_batches_completed']
        assert [row['title'] for row in report['items']] == ['rule-1', 'rule-2', 'rule-3']
    run()
    assert len(models) == 3


@pytest.mark.parametrize('stage', ['topics', 'history'])
def test_invalid_parallel_history_result_cannot_publish_and_valid_caches_survive(tmp_path, monkeypatch, stage):
    target, run = prepare_history(tmp_path, monkeypatch, stage)
    barrier, requested = threading.Barrier(3, timeout=5), []

    def generate_invalid(model, data, cache, pending, retry_transient, system):
        number, result = response_for(stage, data)
        barrier.wait()
        if number == '2':
            if stage == 'topics':
                result['sessions'][0]['evidence_ids'] = ['invented-record']
            else:
                result['items'][0]['evidence'][0]['quote'] = 'invented source quote'
        return json.dumps(result), {}, 1, [], {}

    monkeypatch.setattr(history_knowledge, 'generate_summary', generate_invalid)
    with pytest.raises(ValueError, match='evidence|history quote'):
        run()
    published = target / ('SESSION-TOPICS.md' if stage == 'topics' else 'SESSIONS.md')
    assert not published.exists()

    def generate_repaired(model, data, cache, pending, retry_transient, system):
        number, result = response_for(stage, data)
        requested.append(number)
        return json.dumps(result), {}, 1, [], {}

    monkeypatch.setattr(history_knowledge, 'generate_summary', generate_repaired)
    run()
    assert requested == ['2']
    assert published.exists()


@pytest.mark.parametrize(('stage', 'action'), [('topics', 'classify'), ('history', 'distill')])
def test_history_jobs_receive_shared_request_concurrency(tmp_path, monkeypatch, stage, action):
    observed = []

    def process(repo, packs, output, one_batch=False, batch_workers=1):
        observed.append((one_batch, batch_workers))

    monkeypatch.setattr(runner, action, process)
    result = runner.execute_job({'repo': {'name': 'example'}, 'stage': stage}, tmp_path, tmp_path, workers=15)
    assert result == {'state': 'completed', 'progress': True}
    assert observed == [(True, 15)]


@pytest.mark.parametrize('stage', ['topics', 'history'])
@pytest.mark.parametrize('workers', [1, 3])
@pytest.mark.parametrize(('configured_timeout', 'elapsed'), [(None, 1000), ('5400', 4000)])
def test_slow_complete_history_streams_preserve_longer_deadlines(
        tmp_path, monkeypatch, stage, workers, configured_timeout, elapsed):
    if configured_timeout is None:
        monkeypatch.delenv('DISTILL_TIMEOUT', raising=False)
    else:
        monkeypatch.setenv('DISTILL_TIMEOUT', configured_timeout)
    target, run = prepare_history(tmp_path, monkeypatch, stage, workers)
    clock = threading.local()

    def monotonic():
        calls = getattr(clock, 'calls', 0)
        clock.calls = calls + 1
        return 0 if calls == 0 else elapsed

    class Response:
        def __init__(self, result):
            self.result = result

        def iter_lines(self):
            event = {'choices': [{'delta': {'content': json.dumps(self.result)}, 'finish_reason': 'stop'}]}
            yield 'data: ' + json.dumps(event)
            yield 'data: [DONE]'

    def generate(model, data, cache, pending, retry_transient, system):
        _, result = response_for(stage, data)
        clock.calls = 0
        raw = model.read_stream(Response(result), lambda usage: None, lambda: False)
        return raw, {}, 1, [], {}

    monkeypatch.setattr(distill_module.time, 'monotonic', monotonic)
    monkeypatch.setattr(history_knowledge, 'generate_summary', generate)
    result = run()
    assert result['all_sessions_classified' if stage == 'topics' else 'all_selected_batches_completed']
    assert (target / ('SESSION-TOPICS.md' if stage == 'topics' else 'SESSIONS.md')).exists()
