import json
import subprocess

import pytest

from scripts import sync_github_knowledge as sync
from scripts.sync_github_knowledge import checkout


def test_checkout_preserves_dirty_repository_before_any_network_call(tmp_path):
    path = tmp_path / 'example'
    path.mkdir()
    subprocess.run(['git', 'init', str(path)], capture_output=True, check=True)
    subprocess.run(['git', '-C', str(path), 'remote', 'add', 'origin', 'https://github.com/owner/example.git'], check=True)
    (path / 'draft.txt').write_text('uncommitted work', encoding='utf-8')
    result = checkout({'name': 'example', 'default_branch': 'main'}, 'owner', tmp_path, None)
    assert result['status'] == 'failed'
    assert 'local changes' in result['error']
    assert (path / 'draft.txt').read_text(encoding='utf-8') == 'uncommitted work'


@pytest.mark.parametrize('name', ['..', '.', '../escape', 'https://evil', 'a/b'])
def test_checkout_rejects_names_outside_owned_directory(tmp_path, name):
    with pytest.raises(ValueError):
        checkout({'name': name, 'default_branch': 'main'}, 'owner', tmp_path, None)


def test_refresh_keeps_verified_knowledge_when_a_network_check_fails(tmp_path, monkeypatch):
    catalog = tmp_path / 'checkouts.json'
    catalog.write_text(json.dumps({'repositories': [
        {'name': 'example', 'status': 'verified', 'commit': 'last-good', 'branch': 'main',
         'path': str(tmp_path / 'repositories/example'), 'url': 'https://github.com/owner/example.git', 'files': []},
    ]}), encoding='utf-8')
    monkeypatch.setattr(sync, 'discover', lambda *args: {'owner': 'owner', 'repositories': [
        {'name': 'example', 'default_branch': 'main'}]})
    monkeypatch.setattr(sync, 'checkout', lambda *args: {'name': 'example', 'status': 'failed', 'error': 'network failed'})
    monkeypatch.setattr(sync.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    rows = sync.refresh_sources('owner', tmp_path / 'repositories', tmp_path / 'packs', catalog, tmp_path / 'inventory.json')
    assert rows[0]['commit'] == 'last-good' and rows[0]['status'] == 'verified'
    assert rows[0]['sync_status'] == 'failed'
    assert json.loads(catalog.read_text(encoding='utf-8'))['repositories'][0]['commit'] == 'last-good'
