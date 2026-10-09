"""在 Linux Docker 主机部署已校验的独立 release；失败恢复旧容器，保留备份。"""
import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from backup import copy_database


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def inspect(name):
    result = subprocess.run(['docker', 'inspect', name], text=True, capture_output=True)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def identity(name):
    item = inspect(name)
    if not item:
        return None
    return {'id': item['Id'], 'image': item['Image'], 'pid': item['State']['Pid'], 'started_at': item['State']['StartedAt'],
            'status': item['State']['Status'], 'release': item['Config']['Labels'].get('org.opencontainers.image.revision'),
            'ports': item['HostConfig']['PortBindings'], 'mounts': [{key: mount.get(key) for key in ['Source', 'Destination']} for mount in item['Mounts']]}


def smoke(port, release, password, ingest=False, verify_knowledge=True):
    root = f'http://127.0.0.1:{port}'
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, value=None):
        body = json.dumps(value).encode() if value is not None else None
        req = urllib.request.Request(root + path, data=body, headers={'Content-Type': 'application/json', 'X-Requested-With': 'Tracebook'})
        with client.open(req, timeout=10) as response:
            return json.load(response)
    for attempt in range(30):
        try:
            assert request('/health')['release'] == release
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
    request('/api/login', {'password': password})
    assert request('/api/session')['authenticated']
    request('/api/projects')
    knowledge = request('/api/developer-knowledge') if verify_knowledge else {'projects': [], 'ready': 0}
    for project in knowledge['projects']:
        if project['status'] == 'ready':
            with client.open(root + '/api/developer-knowledge/' + project['name'] + '/documents/CODEBASE.md', timeout=10) as document:
                assert project['commit'].encode() in document.read()
    with client.open(root, timeout=5) as response:
        page = response.read().decode()
        assert '<div id="root"' in page
        for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page):
            with client.open(root + asset, timeout=5) as asset_response:
                assert asset_response.status == 200
    if ingest:
        text = '# Deployment\nThe deployment_probe uses immutable releases.\n'
        bundle = {'schema': 1, 'project': 'deployment-smoke', 'branch': 'main', 'commit': release,
                  'exported_at': 'deployment', 'files': [{'path': 'README.md', 'content': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}]}
        imported = request('/api/import', bundle)
        found = request('/api/search', {'project_id': imported['project']['id'], 'question': 'deployment_probe'})
        assert found['sources'][0]['content'] == text.rstrip('\n')
        conversation = request('/api/history/projects', {'name': 'history-deployment-smoke'})
        assert conversation['origin'] == 'conversation' and conversation['commit'] == ''
        checksum = hashlib.sha256(text.encode()).hexdigest()
        record = {'id': checksum, 'role': 'tool-output', 'text': text, 'time': '2026-10-09T00:00:00Z',
                  'provenance': {'file': 'deployment-probe.jsonl', 'line': 1, 'record_sha256': checksum, 'source_sha256': checksum}}
        batch = {'project_id': conversation['id'], 'harness': 'codex', 'native_id': 'deployment-probe',
                 'title': 'deployment probe', 'records': [record], 'auto_distill': False}
        synced = request('/api/history/sync', batch)
        assert request('/api/history/sync', batch)['added'] == 0
        detail = request('/api/history/sessions/' + synced['session']['id'])
        assert detail['total'] == 1 and detail['records'][0]['text'] == text
        history_sources = request('/api/search', {'project_id': conversation['id'], 'question': 'deployment_probe'})['sources']
        assert history_sources[0]['origin'] == 'conversation'
    return {'health': 'ok', 'auth': 'ok', 'frontend_assets': 'ok', 'import_search': 'ok' if ingest else 'candidate-only',
            'knowledge_projects': len(knowledge['projects']), 'knowledge_ready': knowledge['ready']}


def start(name, image, config, data, port):
    data.mkdir(parents=True, exist_ok=True)
    os.chown(data, 10001, 10001)
    return run('docker', 'run', '-d', '--name', name, '--restart', 'unless-stopped', '--memory', '2g', '--cpus', '2',
               '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL', '--stop-timeout', '165',
               '--env-file', str(config), '-v', str(data) + ':/data', '-p', f'127.0.0.1:{port}:8000', image)


def deploy(args):
    root, release_path = args.root.resolve(), args.release_path.resolve()
    revision = release_path.name
    name = args.name
    if not re.fullmatch('[a-z][a-z0-9-]{2,48}', name):
        raise ValueError('容器名称必须是3至49位小写字母、数字或短横线')
    if not re.fullmatch('[a-f0-9]{40}', revision) or release_path.parent != root / 'releases':
        raise ValueError('release 必须位于指定根目录的 releases/<40位Git提交>')
    config = root / 'config.env'
    env = dict(line.split('=', 1) for line in config.read_text().splitlines() if line and not line.startswith('#'))
    if len(env.get('APP_PASSWORD', '')) < 16 or len(env.get('SESSION_SECRET', '')) < 32:
        raise ValueError('独立应用配置缺失')
    manifest = json.loads((release_path / 'package-manifest.json').read_text())
    assert manifest['revision'] == revision
    for file_name, checksum in manifest['files'].items():
        target = (release_path / file_name).resolve()
        assert target.is_relative_to(release_path) and hashlib.sha256(target.read_bytes()).hexdigest() == checksum
    protected = {name: identity(name) for name in args.protect_container}
    assert all(item and item['status'] == 'running' for item in protected.values())
    image = 'tracebook:' + revision
    print('Building candidate ' + revision, flush=True)
    # 只构建已核验文件；避免目录残留或打包时间戳影响构建身份。
    with tempfile.TemporaryFile() as stream:
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for file_name in manifest['files']:
                archive.add(release_path / file_name, arcname=file_name)
        stream.seek(0)
        subprocess.run(['docker', 'build', '--build-arg', 'APP_RELEASE=' + revision, '-t', image, '-'], stdin=stream, check=True)
    assert json.loads(run('docker', 'image', 'inspect', image))[0]['Config']['Labels']['org.opencontainers.image.revision'] == revision
    candidate = name + '-candidate-' + revision[:12]
    if inspect(candidate):
        raise ValueError('候选容器已存在，请先检查上次部署状态')
    candidate_data = root / 'candidate-data' / revision
    knowledge = root / 'data/developer-knowledge'
    if knowledge.is_dir():
        shutil.copytree(knowledge, candidate_data / 'developer-knowledge')
    start(candidate, image, config, candidate_data, args.candidate_port)
    report = {'revision': revision, 'protected_before': protected}
    try:
        report['candidate'] = smoke(args.candidate_port, revision, env['APP_PASSWORD'], ingest=True)
        report['candidate_identity'] = identity(candidate)
    finally:
        run('docker', 'stop', '--time', '165', candidate)
    old = identity(name)
    backup_name = name + '-previous-' + str(int(time.time()))
    report['previous'] = old
    if old:
        report['backup'] = copy_database(root / 'data' / 'knowledge.sqlite', root / 'backups' / (backup_name + '.sqlite'))
        run('docker', 'stop', '--time', '165', name)
        run('docker', 'rename', name, backup_name)
    try:
        start(name, image, config, root / 'data', args.port)
        report['live'] = smoke(args.port, revision, env['APP_PASSWORD'])
        assert report['live']['knowledge_projects'] == report['candidate']['knowledge_projects']
        assert report['live']['knowledge_ready'] == report['candidate']['knowledge_ready']
        report['identity'] = identity(name)
        assert report['identity']['release'] == revision
        assert report['identity']['image'] == report['candidate_identity']['image']
        assert {name: identity(name) for name in protected} == protected
    except Exception:
        if inspect(name):
            run('docker', 'stop', '--time', '165', name)
            run('docker', 'rename', name, name + '-failed-' + str(int(time.time())))
        if old:
            run('docker', 'rename', backup_name, name)
            run('docker', 'start', name)
            smoke(args.port, old['release'], env['APP_PASSWORD'], verify_knowledge=False)
        raise
    report['protected_unchanged'] = True
    (release_path / 'deployment.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary = root / 'current.next'
    temporary.symlink_to(release_path, target_is_directory=True)
    temporary.replace(root / 'current')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release_path', type=Path)
    parser.add_argument('--root', type=Path, default=Path('/opt/devrag'))
    parser.add_argument('--port', type=int, default=8083)
    parser.add_argument('--candidate-port', type=int, default=18083)
    parser.add_argument('--name', default='tracebook')
    parser.add_argument('--protect-container', action='append', default=[])
    deploy(parser.parse_args())
