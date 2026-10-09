import hashlib
import json
import tarfile

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
