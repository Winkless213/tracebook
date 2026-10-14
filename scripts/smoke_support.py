"""只使用合成资料与离线模型，通过真实回环 HTTP 验证重启恢复。"""

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
CANARY = 'synthetic-smoke-credential-123456'
SYMPTOM = '升级后任务队列连接池耗尽，需要定位原因'


class OfflineModel:
    def analyze(self, payload, record):
        assert CANARY not in json.dumps(payload)
        attempt = record('offline-smoke', None, None, 0, {}, None)
        record('offline-smoke', attempt, 'completed', 1, {}, None)
        return {'status': 'insufficient', 'cause_hypotheses': [], 'suggestions': ['核对连接池上限与升级后的并发量'],
                'insufficient_reason': '合成日志没有连接池配置，无法确认根因；此为离线流程验证'}


def serve(data_dir, port):
    import uvicorn

    sys.path.insert(0, str(ROOT))
    from devrag.app import create_app

    app = create_app(data_dir, start_worker=False, support_model=OfflineModel())
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', access_log=False))

    @app.get('/__smoke_identity')
    def identity():
        return {'pid': os.getpid(), 'boot_id': os.environ['APP_RELEASE']}

    @app.post('/__smoke_stop')
    def stop():
        server.should_exit = True
        return {'stopping': True}

    server.run()


def start_server(folder, port, environment):
    log = (folder / 'server.log').open('ab')
    boot_id = 'support-smoke-' + uuid4().hex
    process = subprocess.Popen(
        [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--serve', '--data-dir', str(folder / 'data'), '--port', str(port)],
        cwd=ROOT, env={**environment, 'APP_RELEASE': boot_id}, stdout=log, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    process.smoke_port, process.smoke_boot_id = port, boot_id
    log.close()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=1, trust_env=False) as client:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError('冒烟服务提前退出，检查本轮 server.log')
                try:
                    if client.get('/health').json().get('release') == boot_id:
                        identity = client.get('/__smoke_identity').json()
                        if identity['boot_id'] == boot_id:
                            process.smoke_pid = identity['pid']
                            return process
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
        raise RuntimeError('冒烟服务启动超时')
    except BaseException:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        raise


def stop_server(process):
    if process.poll() is None:
        with httpx.Client(base_url=f'http://127.0.0.1:{process.smoke_port}', timeout=3, trust_env=False) as client:
            assert client.get('/__smoke_identity').json()['boot_id'] == process.smoke_boot_id
            response = client.post('/__smoke_stop', headers={'X-Requested-With': 'Tracebook'})
            response.raise_for_status()
    process.wait(timeout=10)
    assert process.returncode == 0


def run_smoke():
    folder = ROOT / 'output' / 'support-smoke' / uuid4().hex
    folder.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, 'APP_PASSWORD': secrets.token_urlsafe(18), 'SESSION_SECRET': secrets.token_urlsafe(40),
                   'SUPPORT_API_KEY': '', 'SUPPORT_PRIMARY_MODEL': 'deepseek-v4.1-flash',
                   'SUPPORT_FALLBACK_MODEL': 'gemini-3.8-flash-high'}
    process = start_server(folder, port, environment)
    pids = [process.smoke_pid]
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=15, trust_env=False) as client:
            assert client.get('/api/support/cases').status_code == 401
            assert client.post('/api/support/sessions', json={'symptom': SYMPTOM}).status_code == 403
            client.headers['X-Requested-With'] = 'Tracebook'
            assert client.post('/api/login', json={'password': environment['APP_PASSWORD']}).status_code == 200
            project = import_knowledge(client)
            created = client.post('/api/support/sessions', json={'symptom': SYMPTOM, 'project_id': project})
            assert created.status_code == 201
            thread_id = created.json()['thread_id']
            path = '/api/support/sessions/' + thread_id
            assert client.post(path + '/supplement', json={'product_version': 'v4.1.0'}).json()['pending']['missing'] == ['reproduction']
            stop_server(process)
            process = start_server(folder, port, environment)
            pids.append(process.smoke_pid)
            assert client.get(path).json()['pending']['missing'] == ['reproduction']
            response = client.post(path + '/supplement', json={
                'reproduction': '升级并启动消费者后，队列持续增长',
                'log_text': 'ERROR pool exhausted after 30 seconds\nAuthorization: Basic ' + CANARY,
            })
            view = response.json()
            assert view['status'] == 'awaiting_confirmation'
            assert CANARY not in response.text
            assert {'user', 'log', 'knowledge'} <= {item['kind'] for item in view['report']['evidence']}
            assert client.get('/api/support/cases').json() == []
            stop_server(process)
            process = start_server(folder, port, environment)
            pids.append(process.smoke_pid)
            confirmed = client.post(path + '/confirm', json={'resolved': True, 'resolution_note': '调整连接池后已恢复'}).json()
            assert confirmed['status'] == 'resolved'
            assert len(confirmed['attempts']) == 1
            duplicate = client.post(path + '/confirm', json={'resolved': True}).json()
            assert duplicate['case_id'] == confirmed['case_id']
            assert len(client.get('/api/support/cases').json()) == 1
            exported = client.get(path + '/export')
            assert exported.status_code == 200 and CANARY not in exported.text
            (folder / 'report.md').write_text(exported.text, encoding='utf-8')
            invalid = client.post(path + '/supplement', json={'log_text': CANARY + 'x' * 70_000})
            assert invalid.status_code == 422 and CANARY not in invalid.text
            assert client.get('/api/projects').status_code == 200
    finally:
        stop_server(process)
    for path in (folder / 'data').rglob('*'):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes(), path
    result = {'passed': True, 'model_mode': 'offline', 'process_ids': pids, 'restarts': 2,
              'thread_id': thread_id, 'case_id': confirmed['case_id'], 'evidence_kinds': ['user', 'log', 'knowledge'],
              'checks': ['auth', 'partial_resume_after_restart', 'confirm_after_restart', 'case_idempotency',
                         'model_input_redaction', 'disk_redaction', 'validation_error_redaction', 'report_export', 'old_api'],
              'artifact_dir': str(folder), 'server_stopped': process.poll() is not None}
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


def import_knowledge(client):
    content = '# 任务队列\n升级后核对任务队列消费者与连接池上限，数据库超时会导致队列排队。'
    bundle = {'schema': 1, 'project': 'support-smoke', 'branch': 'main', 'commit': 'a' * 40,
              'exported_at': '2026-10-12T00:00:00Z', 'excluded': [],
              'files': [{'path': 'docs/queue.md', 'content': content, 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}]}
    response = client.post('/api/import', json=bundle)
    assert response.status_code == 200
    return response.json()['project']['id']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--port', type=int)
    options = parser.parse_args()
    if options.serve:
        serve(options.data_dir, options.port)
    else:
        run_smoke()
