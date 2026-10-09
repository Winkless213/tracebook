import hashlib
import json

import pytest

from devrag.knowledge_status import history_sources, source_snapshots
from scripts import export_project_history as exporter


def test_missing_history_root_cannot_be_mistaken_for_empty_project_history(tmp_path):
    with pytest.raises(FileNotFoundError):
        exporter.inventory([('codex', tmp_path / 'missing')])


def test_unchanged_exports_do_not_reparse_or_reindex_and_append_is_discovered(tmp_path, monkeypatch):
    original = tmp_path / 'session.jsonl'
    original.write_bytes(b'first\n')
    calls = []

    class Source:
        def __init__(self, *args):
            pass

        def prepare(self):
            calls.append('prepare')
            return {'complete_sha256': hashlib.sha256(original.read_bytes()).hexdigest()}

        def records(self, snapshot):
            calls.append('records')
            return [{'id': snapshot['complete_sha256'], 'role': 'user', 'time': '2026-10-09T00:00:00Z',
                     'text': 'project request', 'provenance': {'line': 1}}]

    monkeypatch.setattr(exporter, 'DejaSource', Source)
    row = {'project': 'example', 'harness': 'codex', 'id': 'session', 'path': str(original),
           'started': '2026-10-09T00:00:00Z', 'association': 'recorded_git_remote', 'cwd': str(tmp_path)}
    first = exporter.export_session(row, tmp_path / 'deja.exe', tmp_path / 'state', tmp_path / 'knowledge')
    assert first['status'] == 'exported' and calls == ['prepare', 'records']
    again = exporter.export_session(row, tmp_path / 'deja.exe', tmp_path / 'state', tmp_path / 'knowledge')
    assert again['cached'] and calls == ['prepare', 'records']
    original.write_bytes(b'first\nsecond\n')
    exporter.export_session(row, tmp_path / 'deja.exe', tmp_path / 'state', tmp_path / 'knowledge')
    assert calls == ['prepare', 'records', 'prepare', 'records']
    assert original.read_bytes() == b'first\nsecond\n'


def test_lost_association_is_removed_from_active_sources_without_deleting_export(tmp_path, monkeypatch):
    project = tmp_path / 'knowledge/example'
    (project / 'history').mkdir(parents=True)
    exported = project / 'history/session.json'
    exported.write_text(json.dumps({'session': {'id': 'session'}, 'source_sha256': 'old'}), encoding='utf-8')
    monkeypatch.setattr(exporter, 'inventory', lambda *args: [])
    exporter.refresh_exports({'owner': 'owner', 'repositories': [{'name': 'example'}]}, [],
                             tmp_path / 'deja.exe', tmp_path / 'state', tmp_path / 'knowledge')
    assert source_snapshots(project) == {} and exported.is_file()


def test_failed_current_export_revokes_previous_ready_source(tmp_path):
    project = tmp_path / 'example'
    (project / 'history').mkdir(parents=True)
    (project / 'history/session.json').write_text(json.dumps({
        'session': {'id': 'session'}, 'source_sha256': 'old'}), encoding='utf-8')
    (project / 'history-sources.json').write_text(json.dumps({'sessions': [
        {'id': 'session', 'status': 'failed', 'source_version': 'new-input'},
    ]}), encoding='utf-8')
    assert source_snapshots(project) != {'session': 'old'}
    with pytest.raises(ValueError, match='incomplete'):
        history_sources(project)


@pytest.mark.parametrize('options', [{'inventory_only': True}, {'projects': ['other']}])
def test_inventory_and_excluded_projects_preserve_active_source_roster(tmp_path, monkeypatch, options):
    project = tmp_path / 'knowledge/example'
    project.mkdir(parents=True)
    roster = project / 'history-sources.json'
    index = project / 'SESSION-INDEX.md'
    roster.write_text(json.dumps({'sessions': [
        {'id': 'session', 'status': 'exported', 'source_version': 'verified-input'},
    ]}), encoding='utf-8')
    index.write_text('Verified exported session index\n', encoding='utf-8')
    before = {path.name: path.read_bytes() for path in (roster, index)}
    monkeypatch.setattr(exporter, 'inventory', lambda *args: [])
    exporter.refresh_exports({'owner': 'owner', 'repositories': [{'name': 'example'}, {'name': 'other'}]}, [],
                             tmp_path / 'deja.exe', tmp_path / 'state', tmp_path / 'knowledge', **options)
    assert {path.name: path.read_bytes() for path in (roster, index)} == before


def test_cached_exports_log_a_summary_without_repeating_every_session(tmp_path, monkeypatch, capsys):
    original = tmp_path / 'session.jsonl'
    original.write_bytes(b'unchanged\n')
    row = {'project': 'example', 'harness': 'codex', 'id': 'session', 'path': str(original),
           'started': '2026-10-09T00:00:00Z', 'association': 'recorded_git_remote', 'cwd': str(tmp_path),
           'bytes': original.stat().st_size}
    monkeypatch.setattr(exporter, 'inventory', lambda *args: [row.copy()])
    monkeypatch.setattr(exporter, 'associate', lambda rows, *args: rows)
    monkeypatch.setattr(exporter, 'export_session', lambda *args: {'status': 'exported', 'cached': True, 'records': 1})
    exporter.refresh_exports({'owner': 'owner', 'repositories': [{'name': 'example'}]}, [],
                             tmp_path / 'deja.exe', tmp_path / 'state', tmp_path / 'knowledge')
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 1
    assert events[0]['exports'] == {'cached': 1}
