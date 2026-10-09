"""只发布已完成的项目知识；原始资料和生成缓存保留在本机。"""
import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.knowledge_status import DOCUMENT_STAGES, stage_ready


def package(root, output):
    root = root.resolve()
    catalog_bytes = (root / 'catalog.json').read_bytes()
    catalog = json.loads(catalog_bytes)
    documents = {'catalog.json': catalog_bytes, 'INDEX.md': (root / 'INDEX.md').read_bytes()}
    ready = 0
    for row in catalog['projects']:
        if not row['documents']:
            continue
        project = (root / row['name']).resolve()
        if not project.is_relative_to(root) or project == root:
            raise ValueError('Invalid project path')
        if not set(row['documents']) <= DOCUMENT_STAGES.keys():
            raise ValueError('Unpublished document requested')
        payloads, checksums = {}, {}
        if any(DOCUMENT_STAGES[name] == 'code' for name in row['documents']):
            payloads['provenance.json'] = (project / 'provenance.json').read_bytes()
            proof = json.loads(payloads['provenance.json'])
            if row['status'] != 'ready' or proof['commit'] != row['commit'] or 'CODEBASE.md' not in row['documents']:
                raise ValueError('Knowledge commit or content differs from provenance')
            checksums['CODEBASE.md'] = proof['codebase_sha256']
            if proof.get('modules_sha256'):
                checksums['MODULES.md'] = proof['modules_sha256']
            ready += 1
        if 'SESSION-TOPICS.md' in row['documents']:
            if row.get('topics_status') != 'ready' or not stage_ready(row, project, 'topics'):
                raise ValueError('Topic content or sources differ from provenance')
            payloads['session-topics.json'] = (project / 'session-topics.json').read_bytes()
            checksums['SESSION-TOPICS.md'] = json.loads(payloads['session-topics.json'])['markdown_sha256']
        if 'SESSIONS.md' in row['documents']:
            if row.get('history_status') not in {'ready', 'reviewed'} or not stage_ready(row, project, 'history'):
                raise ValueError('History content or sources differ from provenance')
            payloads['history-provenance.json'] = (project / 'history-provenance.json').read_bytes()
            proof = json.loads(payloads['history-provenance.json'])
            checksums.update({'SESSIONS.md': proof['sessions_sha256'], 'SESSION-TOPICS.md': proof['topics_sha256']})
            if 'SESSION-TOPICS.md' not in row['documents']:
                raise ValueError('Unpublished document requested')
        for name in row['documents']:
            payloads[name] = (project / name).read_bytes()
        for name, checksum in checksums.items():
            if hashlib.sha256(payloads[name]).hexdigest() != checksum:
                raise ValueError('Knowledge content differs from provenance')
        documents.update({row['name'] + '/' + name: data for name, data in payloads.items()})
    manifest = {'projects': len(catalog['projects']), 'ready': ready,
                'topics_ready': sum(row.get('topics_status') == 'ready' for row in catalog['projects']),
                'history_ready': sum(row.get('history_status') == 'ready' for row in catalog['projects']),
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in documents.items()}}
    if (root / 'catalog.json').read_bytes() != catalog_bytes:
        raise RuntimeError('Catalog changed while packaging; retry from a fresh snapshot')
    documents['knowledge-manifest.json'] = json.dumps(manifest, indent=2).encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, data in documents.items():
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), 0o644
            archive.addfile(item, io.BytesIO(data))
    with tarfile.open(output) as archive:
        for name, checksum in manifest['files'].items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != checksum:
                raise ValueError('Archive integrity check failed')
    return {'path': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'manifest_sha256': hashlib.sha256(documents['knowledge-manifest.json']).hexdigest(),
            'projects': manifest['projects'], 'ready': ready,
            'topics_ready': manifest['topics_ready'], 'history_ready': manifest['history_ready']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.root, args.output)))
