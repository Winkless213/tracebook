"""只发布已完成的项目知识；原始资料和生成缓存保留在本机。"""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


def package(root, output):
    root = root.resolve()
    catalog_bytes = (root / 'catalog.json').read_bytes()
    catalog = json.loads(catalog_bytes)
    documents = {'catalog.json': catalog_bytes, 'INDEX.md': (root / 'INDEX.md').read_bytes()}
    ready = 0
    for row in catalog['projects']:
        if row['status'] != 'ready':
            continue
        project = (root / row['name']).resolve()
        if not project.is_relative_to(root) or project == root:
            raise ValueError('Invalid project path')
        provenance = json.loads((project / 'provenance.json').read_text(encoding='utf-8'))
        code = (project / 'CODEBASE.md').read_bytes()
        if provenance['commit'] != row['commit'] or hashlib.sha256(code).hexdigest() != provenance['codebase_sha256']:
            raise ValueError('Knowledge commit or content differs from provenance')
        for name in [*row['documents'], 'provenance.json']:
            if name not in {'CODEBASE.md', 'MODULES.md', 'provenance.json'}:
                raise ValueError('Unpublished document requested')
            documents[row['name'] + '/' + name] = (project / name).read_bytes()
        ready += 1
    manifest = {'projects': len(catalog['projects']), 'ready': ready,
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
            'projects': manifest['projects'], 'ready': ready}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.root, args.output)))
