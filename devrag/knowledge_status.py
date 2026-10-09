"""根据来源版本和内容哈希判定知识状态；代码与会话分别验真。"""
import hashlib
import json
from functools import lru_cache

DOCUMENT_STAGES = {'CODEBASE.md': 'code', 'MODULES.md': 'code',
                   'SESSION-TOPICS.md': 'topics', 'SESSIONS.md': 'history'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def matches(path, checksum):
    return bool(checksum and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum)


@lru_cache(maxsize=2048)
def source_identity(path, modified, size):
    # 状态轮询只缓存来源身份，不反复解析体积很大的会话正文。
    source = read_json(path)
    return source['session']['id'], source['source_sha256']


def source_snapshots(target):
    registry = target / 'history-sources.json'
    active = {row['id']: row for row in read_json(registry).get('sessions', [])} if registry.is_file() else None
    result = {}
    for path in (target / 'history').glob('*.json'):
        if active is not None and path.stem not in active:
            continue
        if active is not None and active[path.stem]['status'] == 'failed':
            continue
        stat = path.stat()
        identifier, checksum = source_identity(path, stat.st_mtime_ns, stat.st_size)
        result[identifier] = checksum
    if active is not None:
        for identifier, row in active.items():
            if identifier not in result:
                result[identifier] = 'pending-export:' + row['source_version']
    return result


def history_sources(target):
    snapshots = source_snapshots(target)
    if any(value.startswith('pending-export:') for value in snapshots.values()):
        raise ValueError('Session export is incomplete; original data and completed results preserved')
    return [read_json(target / 'history' / (identifier + '.json')) for identifier in sorted(snapshots)]


def stage_ready(repo, target, stage, snapshots=None):
    if stage == 'code':
        proof = read_json(target / 'provenance.json')
        return bool(proof.get('all_batches_completed') and proof.get('commit') == repo.get('commit')
                    and matches(target / 'CODEBASE.md', proof.get('codebase_sha256')))
    snapshots = source_snapshots(target) if snapshots is None else snapshots
    if not snapshots:
        return False
    if stage == 'topics':
        proof = read_json(target / 'session-topics.json')
        return bool(proof.get('all_sessions_classified') and proof.get('source_commit_for_topic_context') == repo.get('commit')
                    and {row['id']: row['source_sha256'] for row in proof.get('sessions', [])} == snapshots
                    and all(row.get('complete') for row in proof.get('sessions', []))
                    and matches(target / 'SESSION-TOPICS.md', proof.get('markdown_sha256')))
    proof = read_json(target / 'history-provenance.json')
    return bool(proof.get('all_selected_batches_completed') and proof.get('commit') == repo.get('commit')
                and proof.get('source_snapshots') == snapshots and stage_ready(repo, target, 'topics', snapshots)
                and matches(target / 'SESSIONS.md', proof.get('sessions_sha256'))
                and matches(target / 'SESSION-TOPICS.md', proof.get('topics_sha256')))


def project_status(repo, target):
    snapshots = source_snapshots(target)
    available = repo.get('status') == 'verified'
    result = {'status': 'pending' if available else 'unavailable', 'input_files': 0, 'documents': [],
              'history_sessions': len(snapshots), 'history_items': 0,
              'topics_status': 'pending' if snapshots else 'no_sources',
              'history_status': 'pending' if snapshots else 'no_sources'}
    if not available:
        return result
    if stage_ready(repo, target, 'code'):
        proof = read_json(target / 'provenance.json')
        result.update(status='ready', input_files=proof['input_files'], documents=['CODEBASE.md'])
        module = target / 'MODULES.md'
        if proof.get('batches', 1) > 1 and module.is_file() and (
                not proof.get('modules_sha256') or matches(module, proof['modules_sha256'])):
            result['documents'].append('MODULES.md')
    if stage_ready(repo, target, 'topics', snapshots):
        result['topics_status'] = 'ready'
        result['documents'].append('SESSION-TOPICS.md')
    if stage_ready(repo, target, 'history', snapshots):
        proof = read_json(target / 'history-provenance.json')
        result.update(history_status='ready' if proof['items'] else 'reviewed', history_items=proof['items'])
        result['documents'].append('SESSIONS.md')
    return result
