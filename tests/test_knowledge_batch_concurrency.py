import json
import threading

import pytest

from devrag import distill as distill_module
from scripts import build_knowledge as builder
from scripts import run_developer_knowledge as runner


@pytest.mark.parametrize('workers', [10, 15])
def test_one_repository_can_fill_batch_workers_and_preserves_batch_order(workers):
    barrier = threading.Barrier(workers, timeout=5)

    def process(number, batch):
        barrier.wait()
        return number, batch

    assert builder.process_batches(process, list(range(workers)), workers=workers) == [
        (index + 1, index) for index in range(workers)]


def test_batch_failure_preserves_already_running_results_and_stops_queued_requests():
    failed, running, finished = threading.Event(), threading.Event(), []

    def process(number, batch):
        if number == 1:
            assert running.wait(5)
            failed.set()
            raise ValueError('Invalid source')
        assert number == 2
        running.set()
        assert failed.wait(5)
        finished.append(number)
        return number

    with pytest.raises(ValueError, match='Invalid source'):
        builder.process_batches(process, list(range(20)), workers=2)
    assert finished == [2]


def test_code_jobs_receive_the_shared_request_concurrency(tmp_path, monkeypatch):
    seen = []

    def build(repo, packs, output, one_batch=False, batch_workers=1):
        seen.append((one_batch, batch_workers))

    monkeypatch.setattr(runner, 'build', build)
    result = runner.execute_job({'repo': {'name': 'example'}, 'stage': 'code'}, tmp_path, tmp_path, workers=15)
    assert result == {'state': 'completed', 'progress': True}
    assert seen == [(True, 15)]


def test_code_readiness_is_prioritized_without_violating_history_dependencies(tmp_path):
    repo = {'name': 'example', 'commit': 'head', 'status': 'verified'}
    jobs = runner.reconcile_jobs([], [repo], tmp_path)
    assert [job['stage'] for job in jobs] == ['code', 'topics', 'history']


@pytest.mark.parametrize('workers', [1, 2])
@pytest.mark.parametrize(('configured_timeout', 'elapsed'), [(None, 1000), ('5400', 4000)])
def test_slow_complete_code_batches_and_merge_are_not_cut_off_at_fifteen_minutes(
        tmp_path, monkeypatch, workers, configured_timeout, elapsed):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    if configured_timeout is None:
        monkeypatch.delenv('DISTILL_TIMEOUT', raising=False)
    else:
        monkeypatch.setenv('DISTILL_TIMEOUT', configured_timeout)
    repo = {'name': 'example', 'url': 'https://github.com/test/example.git', 'branch': 'main',
            'commit': 'head', 'tracked_files': 2}
    pack = tmp_path / 'packs/example/head'
    pack.mkdir(parents=True)
    (pack / 'metrics.json').write_text(json.dumps({'commit': 'head', 'securityExcluded': []}), encoding='utf-8')
    (pack / 'code.json').write_text(json.dumps({'files': {'a.py': 'a', 'b.py': 'b'}}), encoding='utf-8')
    monkeypatch.setattr(builder, 'make_batches', lambda files: [[{'path': name, 'content': text}]
                                                             for name, text in files.items()])
    clock = threading.local()

    def monotonic():
        calls = getattr(clock, 'calls', 0)
        clock.calls = calls + 1
        return 0 if calls == 0 else elapsed

    class Response:
        def __init__(self, markdown):
            self.markdown = markdown

        def iter_lines(self):
            event = {'choices': [{'delta': {'content': json.dumps({'markdown': self.markdown})}, 'finish_reason': 'stop'}]}
            yield 'data: ' + json.dumps(event)
            yield 'data: [DONE]'

    def summarize(model, data, cache, allowed):
        clock.calls = 0
        markdown = '[[' + next(iter(allowed)) + ']]'
        result = json.loads(model.read_stream(Response(markdown), lambda usage: None, lambda: False))
        return {**result, 'model': 'test-model', 'effort': 'max', 'usage': {},
                'input_sha256': markdown, 'cache_key_sha256': markdown}

    monkeypatch.setattr(distill_module.time, 'monotonic', monotonic)
    monkeypatch.setattr(builder, 'summarize', summarize)
    proof = builder.build(repo, tmp_path / 'packs', tmp_path / 'knowledge', batch_workers=workers)
    assert proof['all_batches_completed']
    assert len(proof['model_runs']) == 3


def test_parallel_code_batches_use_independent_models_then_merge_validated_results(tmp_path, monkeypatch):
    repo = {'name': 'example', 'url': 'https://github.com/test/example.git', 'branch': 'main',
            'commit': 'head', 'tracked_files': 3}
    pack = tmp_path / 'packs/example/head'
    pack.mkdir(parents=True)
    (pack / 'metrics.json').write_text(json.dumps({'commit': 'head', 'securityExcluded': []}), encoding='utf-8')
    (pack / 'code.json').write_text(json.dumps({'files': {'a.py': 'a', 'b.py': 'b', 'c.py': 'c'}}), encoding='utf-8')
    monkeypatch.setattr(builder, 'make_batches', lambda files: [[{'path': name, 'content': text}]
                                                             for name, text in files.items()])
    barrier, guard, models = threading.Barrier(3, timeout=5), threading.Lock(), []

    class Model:
        configured, model, effort = True, 'test-model', 'max'
        timeout = 240

    def summarize(model, data, cache, allowed):
        if 'files' in data:
            with guard:
                models.append(model)
            assert model.yield_between_batches
            barrier.wait()
            name = data['files'][0]['path']
            markdown = '[[' + name + ']]'
        else:
            assert len(data['notes']) == 3
            assert len({id(model) for model in models}) == 3
            markdown = ' '.join(data['notes'])
        return {'markdown': markdown, 'model': 'test-model', 'effort': 'max', 'usage': {},
                'input_sha256': markdown, 'cache_key_sha256': markdown}

    monkeypatch.setattr(builder, 'Distiller', Model)
    monkeypatch.setattr(builder, 'summarize', summarize)
    proof = builder.build(repo, tmp_path / 'packs', tmp_path / 'knowledge', one_batch=True, batch_workers=3)
    assert proof['batches'] == 3
    assert proof['all_batches_completed']
    assert len(proof['model_runs']) == 4
