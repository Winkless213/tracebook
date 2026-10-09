"""只读选定会话，通过固定版本 deja-vu 脱敏索引后增量同步。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .history import HistoryRecord
from .models import SECRET
from .store import digest

DEJA_SHA256 = '85db2a1b094b4b9a7afead17ade22c9b0ef746955e3314efe2e5e730f29c8418'
MARKER = re.compile(r'\[\[tracebook-source:(\d+)\]\](?:\n|$)')
EXCLUDED = 'trae,opencode,aider,amp,gemini,cursor,antigravity,grok,hermes,goose,qwen,kimi,cline,cherrystudio,senpi,gjc,kimchi,commandcode,zcode,kiro,kilocode,roo,crush,continue,pi,prime,omp,openclaw,copilot,copilot-chat,codewhale,codebuddy,reasonix,muse,deepseek,zed,deja'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def marked_text(value, line):
    if not isinstance(value, str) or not value.strip():
        return value
    if MARKER.search(value):
        raise ValueError('原文与连接器来源标记冲突，未同步')
    return f'[[tracebook-source:{line}]]\n' + value


def mark_content(content, line):
    if isinstance(content, str):
        return marked_text(content, line)
    if not isinstance(content, list):
        return content
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') in {'text', 'input_text', 'output_text'}:
            block['text'] = marked_text(block.get('text'), line)
        elif block.get('type') == 'tool_result':
            block['content'] = mark_content(block.get('content'), line)
    return content


def normalize_line(record, harness, line):
    record = json.loads(json.dumps(record, ensure_ascii=False))
    if harness == 'codex':
        payload = record.get('payload', {})
        kind = payload.get('type')
        if record.get('type') == 'response_item' and kind in {'custom_tool_call_output', 'function_call_output'}:
            output = payload.get('output')
            if isinstance(output, list):
                output = '\n'.join(block['text'] for block in output if isinstance(block, dict) and block.get('type') in {'text', 'input_text', 'output_text'} and isinstance(block.get('text'), str))
            if isinstance(output, str) and '\nOutput:\n' in output:
                framing, text = output.split('\nOutput:\n', 1)
                payload['output'] = framing + '\nOutput:\n' + marked_text(text, line)
            else:
                payload['output'] = marked_text(output, line)
        elif record.get('type') == 'response_item' and payload.get('role') in {'user', 'assistant'}:
            payload['content'] = mark_content(payload.get('content'), line)
        elif record.get('type') == 'event_msg' and kind in {'user_message', 'agent_message'}:
            payload['message'] = marked_text(payload.get('message'), line)
        elif record.get('type') == 'compacted':
            # 采集库把无role的message当用户发言；压缩摘要实际是AI生成的历史线索。
            payload['role'] = 'assistant'
            payload['message'] = marked_text(payload.get('message'), line)
    elif record.get('type') in {'user', 'assistant'}:
        message = record.get('message', {})
        message['content'] = mark_content(message.get('content'), line)
    return record


def export_records(messages, snapshot, native_id):
    records, parts = [], {}
    for message in messages:
        role = message.get('role')
        if role not in {'user', 'assistant', 'tool-output'}:
            continue
        text = message.get('text', '')
        found = {int(number) for number in MARKER.findall(text)}
        if len(found) != 1 or not found <= snapshot['lines'].keys():
            raise ValueError('索引记录缺少唯一原文来源，停止同步')
        line = found.pop()
        origin = snapshot['lines'][line]
        text = SECRET.sub('[REDACTED]', MARKER.sub('', text))
        for offset in range(0, len(text), 5000):
            part = parts.get(line, 0)
            parts[line] = part + 1
            record = {'id': digest([native_id, snapshot['source'], line, origin['sha256'], role, part]),
                      'role': role, 'text': text[offset:offset + 5000], 'time': origin['time'],
                      'provenance': {'file': snapshot['source'], 'line': line, 'record_sha256': origin['sha256'],
                                     'source_sha256': snapshot['sha256'], 'part': part}}
            try:
                records.append(HistoryRecord.model_validate(record).model_dump())
            except ValidationError:
                raise ValueError('记录时间、来源或脱敏校验失败，未输出原文，请在本机核查') from None
    return records


def normalize_snapshot(source, destination, harness, progress):
    full_hash, complete_hash, prefix_hash = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    offset, completed, pending, lines = progress.get('copied_bytes', 0), 0, 0, {}
    with source.open('rb') as stream:
        # 固定本轮字节边界，活动会话新追加的内容留给下一轮。
        remaining = os.fstat(stream.fileno()).st_size
        number = 0
        while remaining:
            raw = stream.readline(remaining)
            if not raw:
                break
            remaining -= len(raw)
            full_hash.update(raw)
            if not raw.endswith(b'\n'):
                pending = len(raw)
                break
            prefix_hash.update(raw[:max(0, offset - completed)])
            complete_hash.update(raw)
            completed += len(raw)
            number += 1
            record = json.loads(raw.decode('utf-8'))
            lines[number] = {'sha256': sha(raw), 'time': record.get('timestamp', '')}
            normalized = normalize_line(record, harness, number)
            destination.write(json.dumps(normalized, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n')
    if completed < offset or (offset and prefix_hash.hexdigest() != progress['prefix_sha256']):
        raise ValueError('原始会话已截断或改写，保留旧记录并停止；请使用新的状态目录核查')
    return {'source': str(source), 'sha256': full_hash.hexdigest(), 'complete_sha256': complete_hash.hexdigest(),
            'lines': lines, 'pending_bytes': pending, 'copied_bytes': completed}


class DejaSource:
    def __init__(self, executable, state, source):
        self.executable = Path(executable).resolve()
        self.source = {**source, 'path': str(Path(source['path']).resolve())}
        if source['harness'] not in {'codex', 'claude'}:
            raise ValueError('只支持明确选定的Codex或Claude会话')
        self.root = Path(state).resolve() / digest([self.source['path'], source['harness']])[:24]
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkpoint = self.root / 'checkpoint.json'
        self.progress = json.loads(self.checkpoint.read_text(encoding='utf-8')) if self.checkpoint.exists() else {}
        subdir = 'codex/sessions' if source['harness'] == 'codex' else 'claude/selected'
        self.copy = self.root / subdir / Path(source['path']).name
        if self.copy.resolve() == Path(self.source['path']):
            raise ValueError('索引副本不能覆盖原始会话')

    def prepare(self):
        source = Path(self.source['path'])
        if source.suffix != '.jsonl':
            raise ValueError('本连接器仅支持明确JSONL文件')
        with tempfile.TemporaryFile(mode='w+b', dir=self.root) as staged:
            snapshot = normalize_snapshot(source, staged, self.source['harness'], self.progress)
            staged.seek(0)
            if self.copy.exists():
                with self.copy.open('rb') as previous:
                    for block in iter(lambda: previous.read(1024 * 1024), b''):
                        if staged.read(len(block)) != block:
                            raise ValueError('隔离索引副本已改写，请保留现场核查')
            self.copy.parent.mkdir(parents=True, exist_ok=True)
            with self.copy.open('ab') as stream:
                shutil.copyfileobj(staged, stream, length=1024 * 1024)
        self.progress.update(copied_bytes=snapshot.pop('copied_bytes'), prefix_sha256=snapshot['complete_sha256'])
        write_json(self.checkpoint, self.progress)
        return snapshot

    def environment(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith('DEJA_')}
        env.update({'DEJA_INDEX_DIR': str(self.root / 'index'), 'DEJA_CODEX_ROOT': str(self.root / 'codex'),
                    'DEJA_CLAUDE_ROOT': str(self.root / 'claude'), 'DEJA_XCODE_CODEX_ROOT': str(self.root / 'empty-codex'),
                    'DEJA_XCODE_CLAUDE_ROOT': str(self.root / 'empty-claude'), 'DEJA_STORES': 'codex,claude',
                    'DEJA_OFFLINE': '1', 'DEJA_EMBED': 'off', 'DEJA_EMBED_OFF': '1', 'DEJA_INCLUDE_SUBAGENTS': '0',
                    'DEJA_EXCLUDE_HARNESSES': EXCLUDED, 'XDG_CONFIG_HOME': str(self.root / 'config'),
                    'XDG_CACHE_HOME': str(self.root / 'cache'), 'XDG_DATA_HOME': str(self.root / 'data'), 'NO_COLOR': '1'})
        return env

    def run(self, *args):
        if sha(self.executable.read_bytes()) != DEJA_SHA256:
            raise ValueError('deja-vu二进制不是已核验的Windows v0.21.7版本')
        result = subprocess.run([str(self.executable), *args], cwd=self.root, env=self.environment(), capture_output=True,
                                timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(f'deja-vu {args[0]}失败，退出码{result.returncode}；未输出可能含原文的日志')
        return json.loads(result.stdout.decode('utf-8')) if '--json' in args else None

    def records(self, snapshot):
        self.run('index')
        messages, offset = [], 0
        while True:
            page = self.run('show', self.source['native_id'], '--harness', self.source['harness'], '--json', '--offset', str(offset), '--limit', '200')
            messages.extend(page['session'].get('messages', []))
            count = page['window']['returned']
            offset += count
            if offset >= page['window']['total']:
                break
            if count == 0:
                raise RuntimeError('deja-vu分页没有前进，停止同步')
        return export_records(messages, snapshot, self.source['native_id'])

    def sync(self, api, project_id, auto_distill=False, force=False):
        snapshot = self.prepare()
        if not force and self.progress.get('synced_sha256') == snapshot['complete_sha256']:
            return {'added': 0, 'unchanged': True, 'pending_bytes': snapshot['pending_bytes']}
        records = self.records(snapshot)
        added, session_id = 0, None
        # 单批最多25段，每段5000字符，在3MB请求门槛内。
        for offset in range(0, len(records), 25):
            result = api.request('/api/history/sync', {'project_id': project_id, 'harness': self.source['harness'],
                'native_id': self.source['native_id'], 'title': self.source['title'], 'records': records[offset:offset + 25], 'auto_distill': False})
            added += result['added']
            session_id = result['session']['id']
            if auto_distill and result['added']:
                self.progress['pending_distill'] = True
                write_json(self.checkpoint, self.progress)
        job_id = None
        if auto_distill and self.progress.get('pending_distill') and session_id:
            job_id = api.request('/api/history/sessions/' + session_id + '/distill', {})['id']
            self.progress['pending_distill'] = False
        self.progress.update(synced_sha256=snapshot['complete_sha256'], session_id=session_id, records=len(records), pending_bytes=snapshot['pending_bytes'])
        write_json(self.checkpoint, self.progress)
        return {'session_id': session_id, 'records': len(records), 'added': added, 'job_id': job_id, 'pending_bytes': snapshot['pending_bytes']}


class HistoryAPI:
    def __init__(self, base, password):
        parsed = urlsplit(base)
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('连接器只允许HTTPS或本机回环入口')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('服务地址不能带凭据或查询参数')
        self.password = password
        self.client = httpx.Client(base_url=base.rstrip('/'), headers={'X-Requested-With': 'Tracebook'}, timeout=60, follow_redirects=False, trust_env=False)
        self.login()

    def login(self):
        response = self.post('/api/login', {'password': self.password})
        if response.status_code != 200:
            raise RuntimeError(f'登录失败 HTTP {response.status_code}')

    def request(self, path, value):
        response = self.post(path, value)
        if response.status_code == 401:
            self.login()
            response = self.post(path, value)
        if response.status_code != 200:
            raise RuntimeError(f'同步接口失败 HTTP {response.status_code}；保留游标，可重试')
        return response.json()

    def post(self, path, value):
        try:
            return self.client.post(path, json=value)
        except httpx.RequestError:
            raise RuntimeError('Tracebook连接失败，保留游标，下一轮继续；请检查服务与隧道') from None
