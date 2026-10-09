"""从干净提交生成源码包和逐文件哈希，拒绝混入运行文件。"""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


def package(output):
    repo = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    if git('status', '--porcelain').strip():
        raise SystemExit('项目存在未提交修改，请先提交再打包')
    revision = git('rev-parse', 'HEAD').decode().strip()
    names = git('ls-tree', '-rz', '--name-only', revision).decode('utf-8').strip('\0').split('\0')
    manifest = {'revision': revision, 'files': {}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as destination, tarfile.open(fileobj=destination, mode='w:gz') as archive:
        for name in names:
            payload = git('show', f'{revision}:{name}')
            manifest['files'][name] = hashlib.sha256(payload).hexdigest()
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(payload), 0o644
            archive.addfile(item, io.BytesIO(payload))
        payload = json.dumps(manifest, indent=2).encode('utf-8')
        item = tarfile.TarInfo('package-manifest.json')
        item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))
    with tarfile.open(output) as archive:
        for name, checksum in manifest['files'].items():
            assert hashlib.sha256(archive.extractfile(name).read()).hexdigest() == checksum
    return {'revision': revision, 'path': str(output.resolve()), 'files': len(names), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.output)))
