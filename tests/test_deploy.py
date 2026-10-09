import hashlib
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import deploy


def test_package_paths_cannot_replace_container_namespace(tmp_path, monkeypatch):
    revision = 'a' * 40
    release = tmp_path / 'releases' / revision
    source = release / 'tests/test_operations.py'
    source.parent.mkdir(parents=True)
    source.write_text('fixture', encoding='utf-8')
    (release / 'package-manifest.json').write_text(json.dumps({'revision': revision, 'files': {'tests/test_operations.py': hashlib.sha256(b'fixture').hexdigest()}}), encoding='utf-8')
    (tmp_path / 'config.env').write_text('APP_PASSWORD=fixture-password-long-enough\nSESSION_SECRET=fixture-session-secret-at-least-32-characters\n', encoding='utf-8')
    monkeypatch.setattr(deploy, 'run', lambda *args: json.dumps([{'Config': {'Labels': {'org.opencontainers.image.revision': revision}}}]) if args[1:3] == ('image', 'inspect') else '')
    monkeypatch.setattr(deploy.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'inspect', lambda name: None)
    names = []

    def start(name, *_args):
        names.append(name)
        assert name == 'tracebook-drill-candidate-' + revision[:12]

    def stop_after_candidate(*_args, **_kwargs):
        raise RuntimeError('stop after candidate')
    monkeypatch.setattr(deploy, 'start', start)
    monkeypatch.setattr(deploy, 'smoke', stop_after_candidate)
    with pytest.raises(RuntimeError, match='stop after candidate'):
        deploy.deploy(Namespace(root=tmp_path, release_path=release, name='tracebook-drill', port=18086, candidate_port=18087, protect_container=[]))
    assert len(names) == 1
