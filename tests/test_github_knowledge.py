import subprocess

import pytest

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
