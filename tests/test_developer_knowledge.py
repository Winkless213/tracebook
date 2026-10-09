import json

import pytest

from devrag.developer_knowledge import KnowledgeFiles


def test_only_generated_markdown_can_be_read(tmp_path):
    folder = tmp_path / 'example'
    folder.mkdir()
    (folder / 'CODEBASE.md').write_text('# Entry\nRead src/main.py', encoding='utf-8')
    (tmp_path / 'catalog.json').write_text(json.dumps({'projects': [
        {'name': 'example', 'status': 'ready', 'documents': ['CODEBASE.md']},
        {'name': 'pending', 'status': 'pending', 'documents': []},
    ]}), encoding='utf-8')
    files = KnowledgeFiles(tmp_path)
    assert files.catalog()['ready'] == 1
    assert files.document('example', 'CODEBASE.md').read_text(encoding='utf-8').startswith('# Entry')
    with pytest.raises(KeyError):
        files.document('pending', 'CODEBASE.md')
    with pytest.raises(KeyError):
        files.document('example', 'provenance.json')
    with pytest.raises(KeyError):
        files.document('..', 'CODEBASE.md')


def test_missing_knowledge_is_empty_not_legacy_qa(tmp_path):
    assert KnowledgeFiles(tmp_path).catalog() == {'projects': [], 'ready': 0}


def test_session_documents_have_independent_readiness_from_code(tmp_path):
    folder = tmp_path / 'example'
    folder.mkdir()
    for name in ['SESSION-TOPICS.md', 'SESSIONS.md']:
        (folder / name).write_text('# History', encoding='utf-8')
    (tmp_path / 'catalog.json').write_text(json.dumps({'projects': [
        {'name': 'example', 'status': 'pending', 'topics_status': 'ready', 'history_status': 'ready',
         'documents': ['SESSION-TOPICS.md', 'SESSIONS.md']},
    ]}), encoding='utf-8')
    files = KnowledgeFiles(tmp_path)
    assert files.catalog()['ready'] == 0
    assert files.catalog()['topics_ready'] == 1
    assert files.catalog()['history_ready'] == 1
    assert files.document('example', 'SESSIONS.md').read_text(encoding='utf-8') == '# History'
    assert files.document('example', 'SESSION-TOPICS.md').is_file()
    with pytest.raises(KeyError):
        files.document('example', 'history-knowledge.json')


def test_incomplete_session_document_is_not_downloadable_even_if_listed(tmp_path):
    folder = tmp_path / 'example'
    folder.mkdir()
    (folder / 'SESSIONS.md').write_text('partial result', encoding='utf-8')
    (tmp_path / 'catalog.json').write_text(json.dumps({'projects': [
        {'name': 'example', 'status': 'ready', 'history_status': 'pending', 'documents': ['SESSIONS.md']},
    ]}), encoding='utf-8')
    with pytest.raises(KeyError):
        KnowledgeFiles(tmp_path).document('example', 'SESSIONS.md')


def test_atomic_publication_pointer_is_resolved_for_each_read(tmp_path):
    versions = []
    for version in ['old', 'new']:
        root = tmp_path / version
        (root / 'example').mkdir(parents=True)
        (root / 'example/CODEBASE.md').write_text(version, encoding='utf-8')
        (root / 'catalog.json').write_text(json.dumps({'projects': [
            {'name': 'example', 'status': 'ready', 'documents': ['CODEBASE.md']},
        ]}), encoding='utf-8')
        versions.append(root)
    pointer = tmp_path / 'current'
    try:
        pointer.symlink_to(versions[0], target_is_directory=True)
    except OSError:
        pytest.skip('This Windows account cannot create symbolic links')
    files = KnowledgeFiles(pointer)
    assert files.document('example', 'CODEBASE.md').read_text(encoding='utf-8') == 'old'
    pointer.unlink()
    pointer.symlink_to(versions[1], target_is_directory=True)
    assert files.document('example', 'CODEBASE.md').read_text(encoding='utf-8') == 'new'
