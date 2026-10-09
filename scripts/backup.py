"""在线备份 SQLite（含 WAL）；恢复时同样复制到全新路径，禁止覆盖运行库。"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def copy_database(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if any(Path(str(target) + suffix).exists() for suffix in ['', '-wal', '-shm']):
        raise FileExistsError('目标已存在；请选择全新路径，禁止覆盖运行中的数据库')
    target.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建防止两个备份任务互相覆盖。
    target.touch(exist_ok=False)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('备份完整性检查失败')
        counts = {table: dst.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ['projects', 'snapshots', 'questions']}
    return {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'counts': counts}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    print(json.dumps(copy_database(args.source, args.target)))
