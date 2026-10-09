"""在前台同步配置中明确选定的本机会话，Ctrl+C停止；不安装自启动或hooks。"""
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.connector import DejaSource, HistoryAPI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=30)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    if not 1 <= len(config['sources']) <= 20 or args.interval < 10:
        raise ValueError('选定来源限1至20份，轮询间隔至少10秒')
    state = Path(config['state_dir']).resolve()
    state.mkdir(parents=True, exist_ok=True)
    password = os.getenv('TRACEBOOK_PASSWORD') or getpass.getpass('Tracebook访问密码：')
    api = HistoryAPI(config['url'], password)
    for source in config['sources']:
        if not source.get('project_id'):
            source['project_id'] = api.request('/api/history/projects', {'name': source['project_name']})['id']
    adapters = [DejaSource(config['deja'], state, source) for source in config['sources']]
    with FileLock(state / 'connector.lock', timeout=0):
        first = True
        while True:
            for source, adapter in zip(config['sources'], adapters, strict=True):
                try:
                    result = adapter.sync(api, source['project_id'], auto_distill=source.get('auto_distill', False), force=first)
                    print(json.dumps({'harness': source['harness'], **result}, ensure_ascii=False), flush=True)
                except (RuntimeError, ValueError, OSError) as exc:
                    print(json.dumps({'harness': source['harness'], 'error': type(exc).__name__, 'detail': str(exc)[:300]}, ensure_ascii=False), flush=True)
                    if not args.watch:
                        raise SystemExit(1) from None
            first = False
            if not args.watch:
                break
            time.sleep(args.interval)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('连接器已停止；下次沿用同一配置即可续传。')
