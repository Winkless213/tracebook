"""只读同步选定本地/GitHub分支的代码与文档，不安装自启动。"""
import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.connector import HistoryAPI
from devrag.repository_connector import RepositorySource
from devrag.store import digest, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--force', action='store_true', help='首次执行重新核对并导入，用于服务端恢复之后')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    sources = config['repositories']
    if not 1 <= len(sources) <= 20:
        raise ValueError('仓库来源限1至20项')
    targets = [(source['project'], source['branch']) for source in sources]
    if len(set(targets)) != len(targets):
        raise ValueError('同一个项目分支不能由两个来源交替覆盖；请只选一个来源')
    state = Path(config['state_dir']).resolve() / digest(config['url'])[:16]
    state.mkdir(parents=True, exist_ok=True)
    adapters = [RepositorySource(state, source) for source in sources]
    intervals = [int(source.get('interval_seconds', 300 if source['kind'] == 'github' else 30)) for source in sources]
    if any(interval < 30 for interval in intervals):
        raise ValueError('检查间隔至少30秒')
    password = os.getenv('TRACEBOOK_PASSWORD') or getpass.getpass('Tracebook访问密码：')
    api = HistoryAPI(config['url'], password)
    with FileLock(state / 'repositories.lock', timeout=0):
        due = [0.0] * len(sources)
        first, failed = True, False
        while True:
            for index, adapter in enumerate(adapters):
                if time.monotonic() < due[index]:
                    continue
                try:
                    value = adapter.sync(api, force=args.force and first)
                    print(json.dumps({'checked_at': now(), **value}, ensure_ascii=False), flush=True)
                except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
                    failed = True
                    # 校验错误可能包含源码，日志仅列类别，避免泄露输入内容。
                    print(json.dumps({'checked_at': now(), 'project': adapter.source['project'], 'branch': adapter.source['branch'],
                                      'error': type(exc).__name__, 'detail': '同步失败，保留上次成功资料；核对网络、分支、采集范围和服务'},
                                     ensure_ascii=False), flush=True)
                due[index] = time.monotonic() + intervals[index]
            first = False
            if not args.watch:
                return 1 if failed else 0
            time.sleep(max(0.1, min(min(due) - time.monotonic(), 1.0)))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('仓库同步已停止；下次沿用相同配置即可继续。')
