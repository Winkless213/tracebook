import subprocess

import pytest

from devrag.repository_connector import RepositorySource
from devrag.store import Store


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE, text=True).strip()


def commit(repo, files):
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    commit(repo, {'README.md': '# 项目\n缓存读取数据库。', 'app/cache.py': 'def load_cache():\n    return 1\n',
                  'docs/design.md': '# 缓存\n读取数据库。'})
    return repo


class API:
    def __init__(self, tmp_path):
        self.store = Store(tmp_path / 'service.sqlite')
        self.calls = 0

    def request(self, path, data):
        from devrag.models import Bundle
        assert path == '/api/import'
        self.calls += 1
        return self.store.ingest(Bundle.model_validate(data))


def local_config(repo):
    return {'kind': 'local', 'path': str(repo), 'project': 'example', 'branch': 'main',
            'includes': ['README.md', 'app', 'docs']}


def test_local_new_commit_updates_code_documents_and_deletions(repository, tmp_path):
    api = API(tmp_path)
    source = RepositorySource(tmp_path / 'state', local_config(repository))
    first = source.sync(api)
    new_commit = commit(repository, {'app/cache.py': 'def load_cache():\n    return 2\n',
                                    'docs/design.md': None, 'docs/current.md': '缓存配置已更新。'})
    second = source.sync(api)
    assert second['commit'] == new_commit and second['snapshot_id'] != first['snapshot_id']
    assert second['changes'] == {'added': 1, 'modified': 1, 'removed': 1}
    assert api.store.source(first['snapshot_id'], 'docs/design.md')['content'].endswith('读取数据库。')
    assert api.store.source(second['snapshot_id'], 'app/cache.py')['content'].endswith('return 2\n')


def test_unchanged_and_restart_do_not_reimport_or_read_dirty_content(repository, tmp_path):
    api = API(tmp_path)
    config = local_config(repository)
    source = RepositorySource(tmp_path / 'state', config)
    first = source.sync(api)
    (repository / 'app/cache.py').write_text('uncommitted_value = 8\n', encoding='utf-8')
    (repository / 'private.md').write_text('untracked private material', encoding='utf-8')
    status = git(repository, 'status', '--porcelain')
    assert source.sync(api)['unchanged']
    assert RepositorySource(tmp_path / 'state', config).sync(api)['unchanged']
    assert api.calls == 1 and git(repository, 'status', '--porcelain') == status
    assert 'uncommitted' not in api.store.source(first['snapshot_id'], 'app/cache.py')['content']


def test_offline_upload_does_not_advance_checkpoint(repository, tmp_path):
    source = RepositorySource(tmp_path / 'state', local_config(repository))
    class Offline:
        def request(self, path, data):
            raise RuntimeError('offline')
    with pytest.raises(RuntimeError, match='offline'):
        source.sync(Offline())
    assert not source.checkpoint.exists()
    assert not source.sync(API(tmp_path))['unchanged']


def test_local_branch_switch_rejected_instead_of_rebinding_project(repository, tmp_path):
    source = RepositorySource(tmp_path / 'state', local_config(repository))
    git(repository, 'checkout', '-b', 'different')
    with pytest.raises(ValueError, match='分支'):
        source.sync(API(tmp_path))


def test_github_fetch_is_isolated_and_tracks_new_remote_commits(repository, tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'clone', '--bare', str(repository), str(remote)], check=True, capture_output=True)
    url = 'https://github.com/example/project.git'
    original = subprocess.run
    def local_transport(args, **kwargs):
        return original([str(remote) if arg == url else arg for arg in args], **kwargs)
    monkeypatch.setattr('devrag.repository_connector.subprocess.run', local_transport)
    config = {'kind': 'github', 'url': url, 'project': 'example', 'branch': 'main',
              'includes': ['README.md', 'app', 'docs']}
    api = API(tmp_path)
    source = RepositorySource(tmp_path / 'state', config)
    first = source.sync(api)
    local_before = git(repository, 'rev-parse', 'HEAD')
    updated = commit(repository, {'docs/design.md': '# 新设计\n缓存不再使用。'})
    git(repository, 'push', str(remote), 'main')
    result = source.sync(api)
    assert result['commit'] == updated and result['snapshot_id'] != first['snapshot_id']
    assert source.mirror.is_relative_to(tmp_path / 'state')
    assert git(repository, 'rev-parse', 'HEAD') == updated != local_before
    assert git(repository, 'remote') == ''
    assert source.sync(api)['unchanged'] and api.calls == 2
    checkpoint = source.checkpoint.read_bytes()
    def network_down(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, '', 'private transport diagnostics')
    monkeypatch.setattr('devrag.repository_connector.subprocess.run', network_down)
    with pytest.raises(RuntimeError, match='ls-remote失败') as error:
        source.sync(api)
    assert 'private' not in str(error.value)
    assert source.checkpoint.read_bytes() == checkpoint and api.calls == 2


@pytest.mark.parametrize('url', ['https://user:password@github.com/example/project.git',
                               'https://github.com/example/project.git?token=secret',
                               'https://untrusted.invalid/example/project.git', '-u'])
def test_remote_rejects_credentials_and_unapproved_hosts(tmp_path, url):
    with pytest.raises(ValueError, match='GitHub'):
        RepositorySource(tmp_path / 'state', {'kind': 'github', 'url': url, 'project': 'example',
                                           'branch': 'main', 'includes': ['README.md']})
