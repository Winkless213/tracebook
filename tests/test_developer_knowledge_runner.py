import hashlib
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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

    def build(repo, packs, output, one_batch=False, **kwargs):
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


def test_repaired_model_configuration_requeues_jobs_blocked_by_the_old_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('DISTILL_MODEL', 'gpt-6-astra')
    repos = [{'name': 'example', 'commit': 'head', 'status': 'verified'}]
    previous = runner.reconcile_jobs([], repos, tmp_path)
    for job in previous:
        job['state'], job['error'] = 'needs_fix', 'Old model unavailable'
    unchanged = runner.reconcile_jobs(previous, repos, tmp_path)
    assert all(job['state'] == 'needs_fix' for job in unchanged)
    monkeypatch.setenv('DISTILL_MODEL', 'gpt-6.1-sol')
    resumed = runner.reconcile_jobs(previous, repos, tmp_path)
    assert all(job['state'] == 'queued' and 'error' not in job for job in resumed)
    assert all(old['version'] != new['version'] for old, new in zip(previous, resumed))


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


@pytest.mark.parametrize('workers', [3, 10, 15])
def test_projects_run_at_requested_concurrency_and_publish_after_requests_finish(tmp_path, monkeypatch, workers):
    names = [f'project-{index}' for index in range(workers + 1)]
    repos = [{'name': name, 'commit': 'head', 'status': 'verified'} for name in names]
    barrier, guard = threading.Barrier(workers, timeout=5), threading.Lock()
    active, completed, peak, published, refreshed = set(), set(), [], [], []

    def build(repo, packs, output, one_batch=False, **kwargs):
        assert one_batch
        with guard:
            active.add(repo['name'])
            peak.append(len(active))
        if repo['name'] != names[-1]:
            barrier.wait()
        with guard:
            active.remove(repo['name'])
            completed.add(repo['name'])

    def publish():
        assert not active
        published.append(len(completed))

    def refresh(previous):
        assert not active
        refreshed.append(len(completed))
        return previous

    monkeypatch.setattr(runner, 'build', build)
    monkeypatch.setattr(runner, 'ready', lambda repo, stage, output: stage != 'code' or repo['name'] in completed)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    state = tmp_path / 'worker.json'
    runner.run(repos, tmp_path, tmp_path, state, workers=workers, refresh=refresh, publish=publish)
    report = json.loads(state.read_text(encoding='utf-8'))
    assert max(peak) == workers
    assert completed == set(names)
    assert report['concurrency'] == workers
    assert report['active'] == []
    assert report['ready']['code'] == len(names)
    assert published[-1] == len(names)
    assert refreshed


def test_same_project_stages_never_overlap_and_history_waits_for_topics(tmp_path, monkeypatch):
    repos = [{'name': name, 'commit': 'head', 'status': 'verified'} for name in ['first', 'second']]
    guard, started = threading.Lock(), threading.Event()
    active, completed, visited = set(), set(), []
    for repo in repos:
        history = tmp_path / repo['name'] / 'history'
        history.mkdir(parents=True)
        (history / 'session.json').write_text(json.dumps({
            'session': {'id': 'session'}, 'source_sha256': 'source'}), encoding='utf-8')

    def action(stage):
        def process(repo, packs, output, one_batch=False, **kwargs):
            name = repo['name']
            with guard:
                assert name not in active
                if stage == 'history':
                    assert (name, 'topics') in completed
                active.add(name)
                visited.append((name, stage))
                if len(active) == 2:
                    started.set()
            assert started.wait(5)
            with guard:
                active.remove(name)
                completed.add((name, stage))
        return process

    monkeypatch.setattr(runner, 'classify', action('topics'))
    monkeypatch.setattr(runner, 'distill', action('history'))
    monkeypatch.setattr(runner, 'build', action('code'))
    monkeypatch.setattr(runner, 'ready', lambda repo, stage, output: (repo['name'], stage) in completed)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    runner.run(repos, tmp_path, tmp_path, tmp_path / 'worker.json', workers=3)
    assert len(visited) == len(set(visited)) == 6


def test_idle_slot_is_refilled_without_waiting_for_a_slow_project(tmp_path, monkeypatch):
    repos = [{'name': name, 'commit': 'head', 'status': 'verified'} for name in ['slow', 'fast', 'replacement']]
    replacement_started, slow_started = threading.Event(), threading.Event()
    completed = set()

    def build(repo, packs, output, one_batch=False, **kwargs):
        if repo['name'] == 'slow':
            slow_started.set()
            assert replacement_started.wait(5)
        elif repo['name'] == 'fast':
            assert slow_started.wait(5)
        else:
            replacement_started.set()
        completed.add(repo['name'])

    monkeypatch.setattr(runner, 'build', build)
    monkeypatch.setattr(runner, 'ready', lambda repo, stage, output: stage != 'code' or repo['name'] in completed)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    runner.run(repos, tmp_path, tmp_path, tmp_path / 'worker.json', workers=2)
    assert completed == {'slow', 'fast', 'replacement'}


def test_graceful_drain_finishes_active_batches_without_starting_more(tmp_path, monkeypatch):
    repos = [{'name': name, 'commit': 'head', 'status': 'verified'} for name in ['running', 'remaining']]
    visited, completed = [], set()

    def build(repo, packs, output, one_batch=False, **kwargs):
        visited.append(repo['name'])
        (output / 'DRAIN').write_text('Reload concurrency\n', encoding='utf-8')
        assert not (output / 'STOP').exists()
        completed.add(repo['name'])

    monkeypatch.setattr(runner, 'build', build)
    monkeypatch.setattr(runner, 'ready', lambda repo, stage, output: stage != 'code' or repo['name'] in completed)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    state = tmp_path / 'worker.json'
    runner.run(repos, tmp_path, tmp_path, state, workers=1)
    assert visited == ['running']
    assert json.loads(state.read_text(encoding='utf-8'))['ready']['code'] == 1


@pytest.mark.parametrize('workers', [3, 10, 15])
def test_request_limit_matches_worker_concurrency(tmp_path, monkeypatch, workers):
    from scripts import build_knowledge as builder

    repo = {'name': 'example', 'commit': 'head', 'status': 'verified'}
    monkeypatch.setattr(runner, 'ready', lambda *args: True)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    runner.run([repo], tmp_path, tmp_path, tmp_path / 'worker.json', workers=workers)
    barrier = threading.Barrier(workers, timeout=5)

    def request():
        with builder.MODEL_SLOTS:
            barrier.wait()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(request) for _ in range(workers)]
        for future in futures:
            future.result()


def test_nonpositive_concurrency_is_rejected_before_starting_work(tmp_path):
    with pytest.raises(ValueError, match='workers'):
        runner.run([], tmp_path, tmp_path, tmp_path / 'worker.json', workers=0)


def test_worker_state_reports_every_live_job(tmp_path, monkeypatch):
    repos = [{'name': name, 'commit': 'head', 'status': 'verified'} for name in ['first', 'second']]
    started, release = threading.Barrier(3, timeout=5), threading.Event()
    completed = set()

    def build(repo, packs, output, one_batch=False, **kwargs):
        started.wait()
        assert release.wait(5)
        completed.add(repo['name'])

    monkeypatch.setattr(runner, 'build', build)
    monkeypatch.setattr(runner, 'ready', lambda repo, stage, output: stage != 'code' or repo['name'] in completed)
    monkeypatch.setattr(runner, 'write_index', lambda *args: None)
    state = tmp_path / 'worker.json'
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run, repos, tmp_path, tmp_path, state, workers=2)
        try:
            started.wait()
            report = json.loads(state.read_text(encoding='utf-8'))
            assert {row['project'] for row in report['active']} == {'first', 'second'}
            assert report['concurrency'] == 2
        finally:
            release.set()
        future.result()


def test_busy_worker_lock_rejects_a_second_executor_before_any_model_work(tmp_path, monkeypatch):
    from filelock import FileLock, Timeout

    visited = []
    monkeypatch.setattr(runner, 'build', lambda *args, **kwargs: visited.append('request'))
    repo = {'name': 'example', 'commit': 'head', 'status': 'verified'}
    with FileLock(tmp_path / 'generation-run.lock', timeout=0), pytest.raises(Timeout):
        runner.run([repo], tmp_path, tmp_path, tmp_path / 'worker.json', workers=10)
    assert not visited
