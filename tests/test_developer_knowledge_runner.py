import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_developer_knowledge as runner
from scripts.build_knowledge import GenerationPending, RecoverableStreamError


@pytest.mark.parametrize('script', ['run_developer_knowledge.py', 'classify_project_history.py',
                                  'distill_project_history.py', 'package_knowledge.py', 'build_knowledge.py'])
def test_knowledge_cli_runs_outside_repository_without_requesting_inference(tmp_path, script):
    entry = Path(runner.__file__).with_name(script)
    result = subprocess.run([sys.executable, '-X', 'utf8', str(entry), '--help'], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout


def test_failed_project_yields_the_pass_to_another_project(tmp_path, monkeypatch):
    jobs = [{'repo': {'name': name}, 'stage': 'topics'} for name in ['failing', 'working']]
    visited, completed = [], set()
    for name in ['failing', 'working']:
        history = tmp_path / name / 'history'
        history.mkdir(parents=True)
        (history / 'session.json').write_text(json.dumps({
            'session': {'id': 'session'}, 'source_sha256': 'source'}), encoding='utf-8')

    def classify(repo, packs, output, one_batch=False):
        visited.append(repo['name'])
        assert one_batch
        if repo['name'] == 'failing':
            raise RecoverableStreamError('Temporary upstream failure')
        completed.add(repo['name'])

    monkeypatch.setattr(runner, 'classify', classify)
    monkeypatch.setattr(runner, 'ready', lambda repo, stage, output: repo['name'] in completed)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    state = tmp_path / 'worker.json'
    runner.RUN_ABORT.clear()
    assert runner.run_pass(jobs, tmp_path, tmp_path, state)
    assert visited == ['failing', 'working']
    assert jobs[0]['state'] == 'retry_wait'
    assert json.loads(state.read_text(encoding='utf-8'))['ready']['topics'] == 1


@pytest.mark.parametrize(('failure', 'expected'), [
    (GenerationPending, ['priority', 'priority', 'other']),
    (RecoverableStreamError, ['priority', 'other', 'priority']),
])
def test_priority_progress_continues_but_temporary_failure_yields(tmp_path, monkeypatch, failure, expected):
    repos = [{'name': name, 'commit': 'head', 'status': 'verified'} for name in ['priority', 'other']]
    visited, completed = [], set()

    def build(repo, packs, output, one_batch=False):
        assert one_batch
        visited.append(repo['name'])
        if repo['name'] == 'priority' and visited.count('priority') == 1:
            raise failure('First batch yielded')
        completed.add(repo['name'])

    monkeypatch.setattr(runner, 'build', build)
    monkeypatch.setattr(runner, 'ready', lambda repo, stage, output: stage != 'code' or repo['name'] in completed)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    runner.run(repos, tmp_path, tmp_path, tmp_path / 'worker.json')
    assert visited == expected


def test_topic_readiness_is_revoked_when_an_exported_source_changes(tmp_path):
    project = tmp_path / 'example'
    history = project / 'history'
    history.mkdir(parents=True)
    source = history / 'session.json'
    source.write_text(json.dumps({'session': {'id': 'session'}, 'source_sha256': 'old'}), encoding='utf-8')
    document = project / 'SESSION-TOPICS.md'
    document.write_text('# Topics', encoding='utf-8')
    (project / 'session-topics.json').write_text(json.dumps({
        'all_sessions_classified': True, 'source_commit_for_topic_context': 'head',
        'sessions': [{'id': 'session', 'source_sha256': 'old', 'complete': True}],
        'markdown_sha256': hashlib.sha256(document.read_bytes()).hexdigest()}), encoding='utf-8')
    repo = {'name': 'example', 'commit': 'head'}
    assert runner.ready(repo, 'topics', tmp_path)
    source.write_text(json.dumps({'session': {'id': 'session'}, 'source_sha256': 'new'}), encoding='utf-8')
    assert not runner.ready(repo, 'topics', tmp_path)


def test_queue_refresh_adds_projects_and_unblocks_only_changed_input(tmp_path):
    repo = {'name': 'example', 'commit': 'head', 'status': 'verified'}
    jobs = runner.reconcile_jobs([], [repo], tmp_path)
    topics = next(job for job in jobs if job['stage'] == 'topics')
    topics['state'] = 'needs_fix'
    unchanged = runner.reconcile_jobs(jobs, [repo], tmp_path)
    assert next(job for job in unchanged if job['stage'] == 'topics')['state'] == 'needs_fix'
    history = tmp_path / 'example/history'
    history.mkdir(parents=True)
    (history / 'session.json').write_text(json.dumps({
        'session': {'id': 'session'}, 'source_sha256': 'appended'}), encoding='utf-8')
    updated = runner.reconcile_jobs(unchanged, [repo, {'name': 'new-project', 'commit': 'new', 'status': 'verified'}], tmp_path)
    assert next(job for job in updated if job['repo']['name'] == 'example' and job['stage'] == 'topics')['state'] == 'queued'
    assert {job['repo']['name'] for job in updated} == {'example', 'new-project'}
