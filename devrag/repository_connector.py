"""同步明确选择的提交；远端读取使用独立裸仓库，不操作开发工作树。"""
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .connector import write_json
from .git_export import export
from .models import BLOCKED
from .store import digest, now

GITHUB = re.compile(r'(?:https://github\.com/|git@github\.com:)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?')


class RepositorySource:
    def __init__(self, state, source):
        self.source = source
        kind = source.get('kind')
        if kind not in {'local', 'github'}:
            raise ValueError('仓库来源须为local或github')
        if not isinstance(source.get('project'), str) or not 1 <= len(source['project']) <= 100:
            raise ValueError('项目名称无效')
        branch = source.get('branch', '')
        if not isinstance(branch, str) or not branch or len(branch) > 150 or branch.startswith('-'):
            raise ValueError('必须选择有效的固定分支')
        self.git(None, 'check-ref-format', 'refs/heads/' + branch)
        includes = source.get('includes')
        if not isinstance(includes, list) or not 1 <= len(includes) <= 200:
            raise ValueError('必须明确选择代码和文档目录')
        for prefix in includes:
            parts = PurePosixPath(prefix).parts if isinstance(prefix, str) else ()
            if not parts or prefix.startswith('/') or '\\' in prefix or ':' in prefix or any(
                part in {'.', '..'} or part.lower() in BLOCKED or part.lower().startswith('.env') for part in parts
            ):
                raise ValueError('采集目录无效或包含敏感目录')
        if kind == 'github' and not GITHUB.fullmatch(source.get('url', '')):
            raise ValueError('仅允许无内嵌凭据的GitHub仓库URL')
        proxy = source.get('proxy')
        if proxy:
            parsed = urlsplit(proxy)
            if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password:
                raise ValueError('代理只允许无凭据的本机HTTP入口')
        self.path = Path(source['path']).resolve(strict=True) if kind == 'local' else None
        identity = {key: source.get(key) for key in ['kind', 'path', 'url', 'project', 'branch', 'includes']}
        self.root = Path(state).resolve() / ('repository-' + digest(identity)[:24])
        self.root.mkdir(parents=True, exist_ok=True)
        self.mirror = self.root / 'mirror.git'
        self.checkpoint = self.root / 'checkpoint.json'
        self.progress = json.loads(self.checkpoint.read_text(encoding='utf-8')) if self.checkpoint.exists() else {}

    def git(self, repo, *args):
        command = ['git']
        if repo is not None:
            command += ['-C', str(repo)]
        if self.source.get('proxy'):
            command += ['-c', 'http.proxy=' + self.source['proxy']]
        command += list(args)
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=90,
                                    env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'})
        except subprocess.TimeoutExpired:
            raise RuntimeError('Git读取超时；保留上次成功快照，后续可重试') from None
        if result.returncode:
            raise RuntimeError(f'Git {args[0]}失败（退出码{result.returncode}）；请核对网络、权限与分支')
        return result.stdout.strip()

    def revision(self):
        if self.source['kind'] == 'local':
            if self.git(self.path, 'branch', '--show-current') != self.source['branch']:
                raise ValueError('本地当前分支与配置不符，停止同步以免归错项目')
            return self.git(self.path, 'rev-parse', '--verify', 'HEAD^{commit}')
        ref = 'refs/heads/' + self.source['branch']
        output = self.git(None, 'ls-remote', '--exit-code', self.source['url'], ref)
        matches = [row.split()[0] for row in output.splitlines() if row.split()[1:] == [ref]]
        if len(matches) != 1 or not re.fullmatch(r'[a-f0-9]{40,64}', matches[0]):
            raise ValueError('远端没有返回唯一可核验的分支提交')
        return matches[0]

    def sync(self, api, force=False):
        revision = self.revision()
        if not force and self.progress.get('commit') == revision:
            return {**self.progress, 'unchanged': True}
        repo = self.path
        if self.source['kind'] == 'github':
            if not self.mirror.exists():
                self.git(None, 'init', '--bare', str(self.mirror))
            self.git(self.mirror, 'fetch', '--quiet', '--no-tags', '--depth=1', self.source['url'], 'refs/heads/' + self.source['branch'])
            if self.git(self.mirror, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}') != revision:
                raise RuntimeError('读取期间远端分支已变化，下次重新核对；未更新知识库')
            repo = self.mirror
        bundle = export(repo, self.source['project'], revision, self.source['includes'])
        bundle.branch = self.source['branch']
        imported = api.request('/api/import', bundle.model_dump(by_alias=True))
        if imported['snapshot']['commit'] != revision:
            raise RuntimeError('服务返回的提交身份不符，未推进同步游标')
        self.progress = {'kind': self.source['kind'], 'project': self.source['project'], 'branch': self.source['branch'],
                         'commit': revision, 'snapshot_id': imported['snapshot']['id'], 'project_id': imported['project']['id'],
                         'files': len(bundle.files), 'excluded': len(bundle.excluded), 'changes': imported['snapshot']['changes'],
                         'synced_at': now()}
        write_json(self.checkpoint, self.progress)
        return {**self.progress, 'unchanged': imported['unchanged']}
