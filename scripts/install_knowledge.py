"""校验整个知识快照后原子切换入口，保留上一版本；Linux 主机使用。"""
import argparse
import hashlib
import json
import re
import tarfile
import time
from pathlib import Path

DOCUMENTS = {'CODEBASE.md', 'MODULES.md', 'SESSION-TOPICS.md', 'SESSIONS.md'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_payloads(payloads):
    manifest = json.loads(payloads['knowledge-manifest.json'])
    if set(payloads) != {*manifest['files'], 'knowledge-manifest.json'} or any(
            sha(payloads[name]) != checksum for name, checksum in manifest['files'].items()):
        raise ValueError('Publication differs from manifest')
    catalog = json.loads(payloads['catalog.json'])
    allowed = {'catalog.json', 'INDEX.md', 'knowledge-manifest.json'}
    for row in catalog['projects']:
        name, documents = row['name'], set(row['documents'])
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in {'.', '..'} or not documents <= DOCUMENTS:
            raise ValueError('Unpublished project or document')
        prefix = name + '/'
        allowed.update(prefix + document for document in documents)
        if documents & {'CODEBASE.md', 'MODULES.md'}:
            allowed.add(prefix + 'provenance.json')
            proof = json.loads(payloads[prefix + 'provenance.json'])
            if row['status'] != 'ready' or proof['commit'] != row['commit'] or 'CODEBASE.md' not in documents:
                raise ValueError('Code identity differs from provenance')
            if sha(payloads[prefix + 'CODEBASE.md']) != proof['codebase_sha256']:
                raise ValueError('Code content differs from provenance')
            if 'MODULES.md' in documents and proof.get('modules_sha256') and sha(payloads[prefix + 'MODULES.md']) != proof['modules_sha256']:
                raise ValueError('Module content differs from provenance')
        if 'SESSION-TOPICS.md' in documents:
            allowed.add(prefix + 'session-topics.json')
            topics = json.loads(payloads[prefix + 'session-topics.json'])
            if row.get('topics_status') != 'ready' or not topics['all_sessions_classified'] or not topics['sessions'] or not all(
                    session['complete'] for session in topics['sessions']):
                raise ValueError('Topic classification is incomplete')
            if topics['source_commit_for_topic_context'] != row['commit'] or sha(payloads[prefix + 'SESSION-TOPICS.md']) != topics['markdown_sha256']:
                raise ValueError('Topic identity or content differs from provenance')
        if 'SESSIONS.md' in documents:
            allowed.add(prefix + 'history-provenance.json')
            proof = json.loads(payloads[prefix + 'history-provenance.json'])
            if row.get('history_status') not in {'ready', 'reviewed'} or not proof['all_selected_batches_completed'] or 'SESSION-TOPICS.md' not in documents:
                raise ValueError('History distillation is incomplete')
            snapshots = {session['id']: session['source_sha256'] for session in topics['sessions']}
            if proof['commit'] != row['commit'] or proof['source_snapshots'] != snapshots:
                raise ValueError('History sources differ from provenance')
            if sha(payloads[prefix + 'SESSIONS.md']) != proof['sessions_sha256'] or sha(payloads[prefix + 'SESSION-TOPICS.md']) != proof['topics_sha256']:
                raise ValueError('History content differs from provenance')
    if set(payloads) != allowed:
        raise ValueError('Unpublished document included in publication')
    expected = {'projects': len(catalog['projects']), 'ready': sum(row['status'] == 'ready' for row in catalog['projects']),
                'topics_ready': sum(row.get('topics_status') == 'ready' for row in catalog['projects']),
                'history_ready': sum(row.get('history_status') == 'ready' for row in catalog['projects'])}
    if any(manifest.get(key, 0) != value for key, value in expected.items()):
        raise ValueError('Publication counts differ from catalog')
    return manifest


def read_archive(path, checksum):
    if sha(path.read_bytes()) != checksum:
        raise ValueError('Archive checksum differs')
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        if len({item.name for item in members}) != len(members) or not all(item.isfile() for item in members):
            raise ValueError('Archive contains duplicate or non-file entries')
        payloads = {item.name: archive.extractfile(item).read() for item in members}
    validate_payloads(payloads)
    return payloads


def snapshot(target):
    catalog = target / 'catalog.json'
    if not catalog.is_file():
        return {'catalog_sha256': 'missing'}
    manifest = (target / 'knowledge-manifest.json').read_bytes()
    result = json.loads(manifest)
    return {'catalog_sha256': sha(catalog.read_bytes()), 'path': str(target.resolve()),
            'manifest_sha256': sha(manifest),
            **{key: result.get(key, 0) for key in ['projects', 'ready', 'topics_ready', 'history_ready']}}


def check_update(target, payloads, expected_catalog):
    current = target / 'catalog.json'
    if (sha(current.read_bytes()) if current.is_file() else 'missing') != expected_catalog:
        raise ValueError('Published catalog changed; read a fresh snapshot')
    if not current.is_file():
        return
    old = json.loads(current.read_bytes())
    incoming = {row['name']: row for row in json.loads(payloads['catalog.json'])['projects']}
    if {row['name'] for row in old['projects']} - incoming.keys():
        raise ValueError('Publication would remove existing projects')
    for row in old['projects']:
        next_row = incoming[row['name']]
        if row['status'] != 'ready' or row['commit'] != next_row['commit']:
            continue
        if next_row['status'] != 'ready':
            raise ValueError('Publication would discard completed code for the same commit')
        for name in set(row['documents']) & {'CODEBASE.md', 'MODULES.md'}:
            relative = row['name'] + '/' + name
            if (target / relative).read_bytes() != payloads.get(relative):
                raise ValueError('Existing code differs for the same commit')


def install(data, archive, checksum, expected_catalog):
    data = data.resolve()
    if not re.fullmatch(r'[a-f0-9]{64}', checksum):
        raise ValueError('Invalid snapshot identity')
    target, releases = data / 'developer-knowledge', data / 'knowledge-releases'
    payloads = read_archive(archive, checksum)
    check_update(target, payloads, expected_catalog)
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not releases.resolve().is_relative_to(data):
        raise ValueError('Knowledge versions point outside the data directory')
    version = releases / checksum
    if version.exists():
        if any((version / name).read_bytes() != content for name, content in payloads.items()):
            raise ValueError('Existing version differs from snapshot')
    else:
        staged = releases / ('.staging-' + str(time.time_ns()))
        staged.mkdir(mode=0o755)
        for name, content in payloads.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(content)
            path.chmod(0o644)
        staged.rename(version)
    check_update(target, payloads, expected_catalog)
    previous = target.resolve() if target.exists() else None
    temporary = data / ('developer-knowledge.next-' + str(time.time_ns()))
    temporary.symlink_to(version.relative_to(data), target_is_directory=True)
    legacy = None
    try:
        if target.exists() and not target.is_symlink():
            if target.resolve().parent != data:
                raise ValueError('Legacy knowledge path is outside the data directory')
            legacy = releases / ('legacy-' + str(time.time_ns()))
            target.rename(legacy)
        temporary.replace(target)
    except Exception:
        if legacy and not target.exists():
            legacy.rename(target)
        raise
    if any((target / name).read_bytes() != content for name, content in payloads.items()):
        if legacy or previous:
            temporary.symlink_to((legacy or previous).relative_to(data), target_is_directory=True)
            temporary.replace(target)
        raise ValueError('Activated knowledge differs; previous version restored')
    return {**snapshot(target), 'previous': str(legacy or previous), 'archive_sha256': checksum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('/opt/devrag/data'))
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--expected-catalog', default='missing')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(snapshot(args.data / 'developer-knowledge')))
        return
    if not args.archive or not args.sha256:
        parser.error('--archive and --sha256 are required')
    import fcntl
    with (args.data / '.knowledge-publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(install(args.data, args.archive, args.sha256, args.expected_catalog)))


if __name__ == '__main__':
    main()
