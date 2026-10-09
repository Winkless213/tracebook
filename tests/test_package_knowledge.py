import hashlib
import json
import tarfile

import pytest

from scripts.package_knowledge import package


def test_publication_includes_only_completed_documents(tmp_path):
    knowledge = tmp_path / 'knowledge'
    project = knowledge / 'example'
    project.mkdir(parents=True)
    code = b'# Entry\ncommit: abc'
    (project / 'CODEBASE.md').write_bytes(code)
    (project / 'provenance.json').write_text(json.dumps({'commit': 'abc', 'codebase_sha256': hashlib.sha256(code).hexdigest()}), encoding='utf-8')
    (project / 'private-original.json').write_text('must not publish', encoding='utf-8')
    (knowledge / 'INDEX.md').write_text('# Index', encoding='utf-8')
    (knowledge / 'catalog.json').write_text(json.dumps({'projects': [
        {'name': 'example', 'commit': 'abc', 'status': 'ready', 'documents': ['CODEBASE.md']},
        {'name': 'pending', 'status': 'pending', 'documents': []},
    ]}), encoding='utf-8')
    archive = tmp_path / 'knowledge.tar.gz'
    report = package(knowledge, archive)
    assert report['projects'] == 2 and report['ready'] == 1
    with tarfile.open(archive) as contents:
        assert set(contents.getnames()) == {'INDEX.md', 'catalog.json', 'example/CODEBASE.md',
                                            'example/provenance.json', 'knowledge-manifest.json'}


def test_publish_history_while_code_generation_is_pending(tmp_path):
    knowledge = tmp_path / 'knowledge'
    project = knowledge / 'example'
    (project / 'history').mkdir(parents=True)
    (project / 'history/session.json').write_text(json.dumps({
        'session': {'id': 'session'}, 'source_sha256': 'source'}), encoding='utf-8')
    topics, lessons = b'# Topics', b'# Lessons'
    (project / 'SESSION-TOPICS.md').write_bytes(topics)
    (project / 'SESSIONS.md').write_bytes(lessons)
    (project / 'session-topics.json').write_text(json.dumps({
        'source_commit_for_topic_context': 'abc', 'all_sessions_classified': True,
        'sessions': [{'id': 'session', 'source_sha256': 'source', 'complete': True}],
        'markdown_sha256': hashlib.sha256(topics).hexdigest()}), encoding='utf-8')
    (project / 'history-provenance.json').write_text(json.dumps({
        'commit': 'abc', 'all_selected_batches_completed': True, 'items': 1,
        'source_snapshots': {'session': 'source'}, 'sessions_sha256': hashlib.sha256(lessons).hexdigest(),
        'topics_sha256': hashlib.sha256(topics).hexdigest()}), encoding='utf-8')
    (knowledge / 'INDEX.md').write_text('# Index', encoding='utf-8')
    (knowledge / 'catalog.json').write_text(json.dumps({'projects': [
        {'name': 'example', 'commit': 'abc', 'status': 'pending', 'topics_status': 'ready',
         'history_status': 'ready', 'documents': ['SESSION-TOPICS.md', 'SESSIONS.md']},
    ]}), encoding='utf-8')
    archive = tmp_path / 'knowledge.tar.gz'
    report = package(knowledge, archive)
    assert report['ready'] == 0 and report['history_ready'] == 1
    with tarfile.open(archive) as contents:
        assert {'example/SESSION-TOPICS.md', 'example/SESSIONS.md', 'example/history-provenance.json'} <= set(contents.getnames())
        assert not any('/history/' in name or name.endswith('history-knowledge.json') for name in contents.getnames())
    (project / 'SESSIONS.md').write_text('unverified replacement', encoding='utf-8')
    with pytest.raises(ValueError, match='provenance'):
        package(knowledge, tmp_path / 'corrupt.tar.gz')
    assert not (tmp_path / 'corrupt.tar.gz').exists()
