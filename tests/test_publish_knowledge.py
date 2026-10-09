import hashlib
import json
import subprocess

import pytest

from scripts import publish_knowledge as publisher


def knowledge(tmp_path):
    source = tmp_path / 'knowledge'
    (source / 'example').mkdir(parents=True)
    code = b'# Entry'
    (source / 'example/CODEBASE.md').write_bytes(code)
    (source / 'example/provenance.json').write_text(json.dumps({
        'commit': 'head', 'codebase_sha256': hashlib.sha256(code).hexdigest()}), encoding='utf-8')
    (source / 'INDEX.md').write_text('# Index', encoding='utf-8')
    (source / 'catalog.json').write_text(json.dumps({'projects': [
        {'name': 'example', 'commit': 'head', 'status': 'ready', 'documents': ['CODEBASE.md']},
    ]}), encoding='utf-8')
    return source


def test_unchanged_knowledge_is_not_uploaded_again_after_verified_publication(tmp_path, monkeypatch):
    source, calls = knowledge(tmp_path), []

    def run(command, **kwargs):
        calls.append(command)
        data = {'catalog_sha256': 'missing'} if '--status' in command else {'ready': 1}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(data))

    monkeypatch.setattr(publisher.subprocess, 'run', run)
    checks = []

    def verify():
        checks.append('api')
        return {'documents_verified': 1}
    state = tmp_path / 'publication.json'
    first = publisher.publish(source, state, 'root@host', verify=verify)
    assert first['state'] == 'published' and checks == ['api']
    count = len(calls)
    assert publisher.publish(source, state, 'root@host', verify=verify)['state'] == 'unchanged'
    assert len(calls) == count and checks == ['api']


def test_failed_readback_does_not_advance_success_state(tmp_path, monkeypatch):
    source = knowledge(tmp_path)
    monkeypatch.setattr(publisher.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, stdout=json.dumps({'catalog_sha256': 'missing'})))

    def verify():
        raise ValueError('readback differs')

    state = tmp_path / 'publication.json'
    with pytest.raises(ValueError, match='readback'):
        publisher.publish(source, state, 'root@host', verify=verify)
    assert not state.exists()
