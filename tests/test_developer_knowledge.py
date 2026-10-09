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
