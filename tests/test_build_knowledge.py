import json

import httpx
import pytest

from devrag.distill import Distiller
from scripts.build_knowledge import RUN_ABORT, make_batches, process_batches, summarize, validate_result, write_index


@pytest.fixture(autouse=True)
def independent_model_run():
    RUN_ABORT.clear()
    yield
    RUN_ABORT.clear()


def test_large_file_and_small_files_are_all_preserved_in_order():
    files = {'app/core.py': 'first\nsecond\nthird\nfourth\n', 'README.md': 'Purpose'}
    batches = make_batches(files, characters=12)
    restored = {path: '' for path in files}
    for batch in batches:
        for item in batch:
            restored[item['path']] += item['content']
    assert restored == files
    assert all(sum(len(item['content']) for item in batch) <= 12 for batch in batches)


def test_model_cannot_publish_a_citation_to_an_unseen_file():
    with pytest.raises(ValueError, match='citation'):
        validate_result({'markdown': '# Architecture\n[[invented.py]]'}, {'app.py'})


def test_citation_whitespace_is_normalized_only_when_real_path_exists():
    assert validate_result({'markdown': 'Entry [[ app.py ]]'}, {'app.py'}) == 'Entry [[app.py]]'


def test_model_result_requires_source_evidence():
    with pytest.raises(ValueError, match='citation'):
        validate_result({'markdown': '# Architecture\nEverything works.'}, {'app.py'})
    assert validate_result({'markdown': '# Architecture\nEntrypoint [[app.py]]'}, {'app.py'})


def test_batch_boundary_handles_one_long_line_and_unicode():
    files = {'中文.py': '开' * 31, '空.md': '', 'b.py': 'b\n'}
    batches = make_batches(files, characters=10)
    assert ''.join(item['content'] for batch in batches for item in batch if item['path'] == '中文.py') == '开' * 31
    assert all(sum(len(item['content']) for item in batch) <= 10 for batch in batches)


def test_http_failure_is_recorded_instead_of_left_running(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(524, request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(httpx.HTTPStatusError):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    progress = json.loads(next(tmp_path.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert progress['state'] == 'failed'
    assert progress['status_code'] == 524
    assert RUN_ABORT.is_set()
    with pytest.raises(InterruptedError):
        summarize(Distiller(), {'repository': {'name': 'another'}, 'files': []}, tmp_path, {'app.py'})


def test_rejected_citations_record_failure_and_preserve_model_output(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    event = {'choices': [{'delta': {'content': json.dumps({'markdown': 'Entry [[missing.py]]'})},
                          'finish_reason': 'stop'}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(ValueError, match='citation'):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    progress = json.loads(next(tmp_path.glob('*.progress.json')).read_text(encoding='utf-8'))
    assert progress['state'] == 'failed'
    assert progress['error_type'] == 'ValueError'
    assert next(tmp_path.glob('*.rejected.json')).exists()


def test_failed_checkout_is_visible_without_claiming_code_was_pulled(tmp_path):
    write_index([{'name': 'broken', 'url': 'https://github.com/owner/broken.git', 'branch': 'main',
                  'status': 'failed', 'error': 'network failure'}], tmp_path)
    row = json.loads((tmp_path / 'catalog.json').read_text(encoding='utf-8'))['projects'][0]
    assert row['status'] == 'unavailable'
    assert row['commit'] is None
    assert row['documents'] == []


def test_upstream_failure_does_not_launch_remaining_paid_batches():
    calls = []

    def failure(index, batch):
        calls.append(index)
        raise RuntimeError('upstream failure')

    with pytest.raises(RuntimeError, match='upstream failure'):
        process_batches(failure, ['first', 'second', 'third'])
    assert calls == [1]


def test_provider_stream_error_prevents_further_requests(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('DISTILL_API_KEY', 'test-key')
    client = httpx.Client
    event = {'error': {'type': 'upstream_error', 'message': 'Upstream HTTP/2 stream failed'}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='data: ' + json.dumps(event) + '\n', request=request))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=transport))
    with pytest.raises(RuntimeError):
        summarize(Distiller(), {'repository': {'name': 'example'}, 'files': []}, tmp_path, {'app.py'})
    assert RUN_ABORT.is_set()
