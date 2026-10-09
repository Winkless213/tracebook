import sqlite3
import subprocess

import pytest
from test_knowledge import bundle

from devrag.store import Store
from scripts.backup import copy_database
from scripts.export_repo import export


def test_export_uses_commit_not_dirty_or_untracked_files(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args])
    git('init', '-b', 'main')
    (tmp_path / 'README.md').write_text('已提交的设计。', encoding='utf-8')
    (tmp_path / '.env').write_text('PRIVATE=example', encoding='utf-8')
    git('add', 'README.md', '.env')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'test: fixture')
    (tmp_path / 'README.md').write_text('未提交的秘密设计。', encoding='utf-8')
    (tmp_path / 'untracked.md').write_text('不允许导出。', encoding='utf-8')
    snapshot = export(tmp_path, 'fixture', 'HEAD', [])
    assert [(file.path, file.content) for file in snapshot.files] == [('README.md', '已提交的设计。')]
    assert snapshot.excluded[0].path == '.env'
    assert git('status', '--porcelain').decode().count('\n') == 2


def test_backup_restores_wal_state_and_refuses_overwrite(tmp_path):
    store = Store(tmp_path / 'live' / 'knowledge.sqlite')
    imported = store.ingest(bundle({'README.md': '缓存每次读取数据库。'}))
    job = store.create_question(imported['project']['id'], '缓存现在采用什么策略？', 'backup-fixture')
    keeper = sqlite3.connect(store.path)
    keeper.execute('PRAGMA journal_mode=WAL')
    try:
        store.update(job['id'], status='generating')
        target = tmp_path / 'backup.sqlite'
        copy_database(store.path, target)
        restored = Store(target)
        assert restored.question(job['id'])['status'] == 'generating'
        assert restored.source(imported['snapshot']['id'], 'README.md')['sha256'] == bundle({'README.md': '缓存每次读取数据库。'}).files[0].sha256
        with pytest.raises(FileExistsError):
            copy_database(store.path, target)
    finally:
        keeper.close()
