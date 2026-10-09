import hashlib
import json

import pytest

from scripts.install_knowledge import check_update, validate_payloads


def publication(history=False):
    code = b'# Entry'
    payloads = {'INDEX.md': b'# Index', 'example/CODEBASE.md': code,
                'example/provenance.json': json.dumps({
                    'commit': 'head', 'codebase_sha256': hashlib.sha256(code).hexdigest()}).encode()}
    row = {'name': 'example', 'commit': 'head', 'status': 'ready', 'documents': ['CODEBASE.md']}
    if history:
        document = b'# Topic'
        row.update(topics_status='ready', documents=['CODEBASE.md', 'SESSION-TOPICS.md'])
        payloads['example/SESSION-TOPICS.md'] = document
        payloads['example/session-topics.json'] = json.dumps({
            'source_commit_for_topic_context': 'head', 'all_sessions_classified': True,
            'sessions': [{'id': 'session', 'source_sha256': 'source', 'complete': True}],
            'markdown_sha256': hashlib.sha256(document).hexdigest()}).encode()
    payloads['catalog.json'] = json.dumps({'projects': [row]}).encode()
    payloads['knowledge-manifest.json'] = json.dumps({
        'projects': 1, 'ready': 1, 'topics_ready': int(history), 'history_ready': 0,
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}).encode()
    return payloads


def test_session_addition_preserves_existing_code_without_rejecting_changed_catalog(tmp_path):
    old, incoming = publication(), publication(history=True)
    for name, data in old.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    validate_payloads(incoming)
    check_update(tmp_path, incoming, hashlib.sha256(old['catalog.json']).hexdigest())
    assert (tmp_path / 'example/CODEBASE.md').read_bytes() == incoming['example/CODEBASE.md']


def test_unreviewed_original_cannot_be_smuggled_into_a_valid_manifest():
    payloads = publication()
    payloads['example/history/raw.md'] = b'private'
    manifest = json.loads(payloads['knowledge-manifest.json'])
    manifest['files']['example/history/raw.md'] = hashlib.sha256(b'private').hexdigest()
    payloads['knowledge-manifest.json'] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match='Unpublished'):
        validate_payloads(payloads)


def test_changed_published_catalog_requires_a_new_snapshot_before_mutation(tmp_path):
    (tmp_path / 'catalog.json').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='catalog changed'):
        check_update(tmp_path, publication(), 'stale')
    assert (tmp_path / 'catalog.json').read_text(encoding='utf-8') == 'changed'
