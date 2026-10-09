"""发布内容有变化的知识快照；认证回读成功后才推进本机成功状态。"""
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from filelock import FileLock

from devrag.knowledge_status import DOCUMENT_STAGES
from scripts.build_knowledge import save_json
from scripts.package_knowledge import package


def fingerprint(root):
    catalog = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
    files = ['catalog.json', 'INDEX.md']
    for row in catalog['projects']:
        project = (root / row['name']).resolve()
        if not project.is_relative_to(root.resolve()) or project == root.resolve() or not set(row['documents']) <= DOCUMENT_STAGES.keys():
            raise ValueError('Invalid knowledge path or document')
        names = list(row['documents'])
        if set(names) & {'CODEBASE.md', 'MODULES.md'}:
            names.append('provenance.json')
        if 'SESSION-TOPICS.md' in names:
            names.append('session-topics.json')
        if 'SESSIONS.md' in names:
            names.append('history-provenance.json')
        files.extend(row['name'] + '/' + name for name in names)
    checksums = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    return hashlib.sha256(json.dumps(checksums, sort_keys=True).encode('utf-8')).hexdigest()


def remote_json(command):
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', timeout=240)
    return json.loads(result.stdout)


def verify_api(root, url, password):
    catalog = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
    documents = []
    with httpx.Client(base_url=url, timeout=30, trust_env=False) as client:
        health = client.get('/health')
        health.raise_for_status()
        if health.json().get('app') != 'devrag' or client.get('/api/developer-knowledge').status_code != 401:
            raise ValueError('Application identity or authentication differs')
        login = client.post('/api/login', json={'password': password}, headers={'X-Requested-With': 'Tracebook'})
        login.raise_for_status()
        response = client.get('/api/developer-knowledge')
        response.raise_for_status()
        live = response.json()
        if live['projects'] != catalog['projects']:
            raise ValueError('Published catalog differs from local snapshot')
        for row in live['projects']:
            prefix = '/api/developer-knowledge/' + quote(row['name'], safe='') + '/documents/'
            for name in row['documents']:
                document = client.get(prefix + quote(name, safe=''))
                document.raise_for_status()
                checksum = hashlib.sha256(document.content).hexdigest()
                if checksum != hashlib.sha256((root / row['name'] / name).read_bytes()).hexdigest():
                    raise ValueError('Published document differs from local snapshot')
                documents.append({'project': row['name'], 'document': name, 'sha256': checksum})
            for name in ['provenance.json', 'session-topics.json', 'history-knowledge.json', 'history-provenance.json']:
                if client.get(prefix + name).status_code != 404:
                    raise ValueError('Private intermediate is downloadable')
    return {'release': health.json()['release'], 'projects': len(live['projects']), 'ready': live['ready'],
            'topics_ready': live.get('topics_ready', 0), 'history_ready': live.get('history_ready', 0),
            'documents_verified': documents, 'unauthenticated_blocked': True, 'private_intermediates_blocked': True}


def publish(root, state, host, remote_root='/opt/devrag', verify=None, force=False):
    if not re.fullmatch(r'[A-Za-z0-9@.:-]+', host) or not re.fullmatch(r'/[A-Za-z0-9/_-]+', remote_root):
        raise ValueError('Invalid publication destination')
    state.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(state.parent / 'knowledge-publication.lock', timeout=0):
        identity = fingerprint(root)
        previous = json.loads(state.read_text(encoding='utf-8')) if state.is_file() else {}
        now = datetime.now(timezone.utc)
        checked = datetime.fromisoformat(previous['checked_at']) if previous.get('checked_at') else None
        if previous.get('fingerprint') == identity and not force:
            if verify and (not checked or (now - checked).total_seconds() >= 300):
                report = {**previous, 'readback': verify(), 'checked_at': now.isoformat()}
                save_json(state, report)
                return {**report, 'state': 'unchanged'}
            return {**previous, 'state': 'unchanged'}
        archives = state.parent / 'knowledge-packages'
        archives.mkdir(exist_ok=True)
        archive = archives / ('knowledge-' + str(time.time_ns()) + '.tar.gz')
        packaged = package(root, archive)
        tool = Path(__file__).with_name('install_knowledge.py')
        tool_name = 'knowledge-install-' + hashlib.sha256(tool.read_bytes()).hexdigest()[:16] + '.py'
        ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host]
        subprocess.run(['scp', '-o', 'BatchMode=yes', str(tool), host + ':' + remote_root + '/' + tool_name], check=True, timeout=120)
        command = [*ssh, 'python3', remote_root + '/' + tool_name, '--data', remote_root + '/data']
        current = remote_json([*command, '--status'])
        installed = current
        if current.get('manifest_sha256') != packaged['manifest_sha256']:
            subprocess.run(['scp', '-o', 'BatchMode=yes', str(archive), host + ':' + remote_root + '/' + archive.name], check=True, timeout=180)
            installed = remote_json([*command, '--archive', remote_root + '/' + archive.name,
                                     '--sha256', packaged['sha256'], '--expected-catalog', current['catalog_sha256']])
        readback = verify() if verify else None
        if fingerprint(root) != identity:
            raise ValueError('Local knowledge changed during publication; retry from a fresh snapshot')
        report = {'state': 'published', 'fingerprint': identity, 'checked_at': now.isoformat(),
                  'package': packaged, 'installed': installed, 'readback': readback}
        save_json(state, report)
        return report
