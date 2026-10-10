"""按项目并发归纳代码和会话；缓存续跑，统一同步和发布。"""
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.knowledge_status import source_snapshots, stage_ready
from devrag.models import SECRET
from scripts.build_knowledge import (
    RUN_ABORT,
    GenerationPending,
    RecoverableStreamError,
    build,
    configure_model_concurrency,
    save_json,
    write_index,
)
from scripts.classify_project_history import classify
from scripts.distill_project_history import distill
from scripts.export_project_history import refresh_exports
from scripts.publish_knowledge import publish as publish_snapshot
from scripts.publish_knowledge import verify_api
from scripts.sync_github_knowledge import refresh_sources


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def ready(repo, stage, output):
    target = output / repo['name']
    if stage != 'code' and not source_snapshots(target):
        return True
    return stage_ready(repo, target, stage)


def reconcile_jobs(previous, repos, output):
    prior = {(row.get('project') or row['repo']['name'], row['stage']): row for row in previous}
    profile = hashlib.sha256(json.dumps({key: os.getenv(key, '') for key in [
        'DISTILL_BASE_URL', 'DISTILL_API_KEY', 'DISTILL_MODEL', 'DISTILL_REASONING_EFFORT', 'DISTILL_API',
        'KNOWLEDGE_REUSE_MODELS']}, sort_keys=True).encode('utf-8')).hexdigest()
    jobs = []
    for repo in repos:
        if repo['status'] != 'verified':
            continue
        snapshots = source_snapshots(output / repo['name'])
        for stage in ['code', 'topics', 'history']:
            version = hashlib.sha256(json.dumps({'commit': repo['commit'], 'profile': profile,
                'sources': snapshots if stage != 'code' else {}}, sort_keys=True).encode('utf-8')).hexdigest()
            old = prior.get((repo['name'], stage), {})
            job = {**old, 'repo': repo, 'stage': stage, 'version': version}
            if old.get('version') != version or (old.get('state') == 'completed' and not ready(repo, stage, output)):
                job['state'] = 'queued'
                job.pop('error', None)
            jobs.append(job)
    return jobs


def update_state(jobs, output, state, current=None, sync=None, workers=1):
    rows = []
    for job in jobs:
        row = {'project': job['repo']['name'], 'stage': job['stage'], 'state': job.get('state', 'queued')}
        if job['stage'] != 'code' and not source_snapshots(output / job['repo']['name']):
            row['state'] = 'no_sources'
        elif ready(job['repo'], job['stage'], output):
            row['state'] = 'completed'
        row.update({key: job[key] for key in ['version', 'error'] if key in job})
        rows.append(row)
    active = [{'project': job['repo']['name'], 'stage': job['stage']} for job in jobs if job.get('state') == 'running']
    report = {'worker_pid': os.getpid(), 'updated_at': datetime.now(timezone.utc).isoformat(),
              'current': current or next(iter(active), None), 'active': active, 'concurrency': workers, 'jobs': rows,
              'complete': bool(rows) and all(row['state'] in {'completed', 'no_sources'} for row in rows),
              'source_sync': sync,
              'ready': {stage: sum(row['state'] == 'completed' and row['stage'] == stage for row in rows)
                        for stage in ['code', 'topics', 'history']}}
    save_json(state, report)
    return report


def eligible(job, output, busy=()):
    repo, stage = job['repo'], job['stage']
    return (repo['name'] not in busy and job.get('state') != 'needs_fix' and not ready(repo, stage, output)
            and (stage != 'history' or ready(repo, 'topics', output)))


def execute_job(job, packs, output, workers=1):
    actions = {'code': build, 'topics': classify, 'history': distill}
    repo, stage = job['repo'], job['stage']
    if (output / 'STOP').exists() or RUN_ABORT.is_set():
        raise InterruptedError('Generation stopped; completed batches preserved')
    result = {'state': 'completed', 'progress': True}
    try:
        options = {'one_batch': True}
        if workers > 1:
            options['batch_workers'] = workers
        actions[stage](repo, packs, output, **options)
    except GenerationPending:
        result['state'] = 'queued'
    except RecoverableStreamError as exc:
        result.update(state='retry_wait', progress=False)
        print(json.dumps({'event': 'job_yielded_for_retry', 'project': repo['name'], 'stage': stage,
                          'error_type': type(exc.__cause__).__name__}), flush=True)
    except InterruptedError:
        raise
    except Exception as exc:
        result.update(state='needs_fix', progress=False, error=SECRET.sub('[REDACTED]', str(exc))[:500])
        print(json.dumps({'event': 'job_needs_fix', 'project': repo['name'], 'stage': stage,
                          'error_type': type(exc).__name__, 'error': result['error']}), flush=True)
    return result


def run_job(job, jobs, packs, output, state, repos=None, sync=None):
    if not eligible(job, output):
        return False
    job['state'] = 'running'
    update_state(jobs, output, state, sync=sync)
    try:
        result = execute_job(job, packs, output)
        job.update({key: value for key, value in result.items() if key != 'progress'})
    except InterruptedError:
        job['state'] = 'interrupted'
        update_state(jobs, output, state, sync=sync)
        raise
    write_index(repos if repos is not None else [job['repo'] for job in jobs if job['stage'] == 'code'], output)
    update_state(jobs, output, state, sync=sync)
    return result['progress']


def run_pass(jobs, packs, output, state):
    progress = False
    for job in jobs:
        progress = run_job(job, jobs, packs, output, state) or progress
    return progress


def run(repos, packs, output, state, watch=False, refresh=None, interval=300, publish=None, workers=1):
    if workers < 1:
        raise ValueError('workers must be positive')
    output.mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    jobs = reconcile_jobs(read(state).get('jobs', []), repos, output)
    cursor, next_refresh, next_publication, sync = 0, 0, 0, None
    active, continuations, needs_publication = {}, [], True
    with FileLock(output / 'generation-run.lock', timeout=0):
        RUN_ABORT.clear()
        configure_model_concurrency(workers)
        write_index(repos, output)
        print(json.dumps({'event': 'generation_worker_started', 'pid': os.getpid(), 'projects': len(repos),
                          'concurrency': workers}), flush=True)
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='knowledge')
        try:
            while True:
                for future in [future for future in active if future.done()]:
                    job = active.pop(future)
                    try:
                        result = future.result()
                    except InterruptedError:
                        job['state'] = 'interrupted'
                        raise
                    job.update({key: value for key, value in result.items() if key != 'progress'})
                    needs_publication = needs_publication or result['state'] == 'completed'
                    if result['progress'] and not ready(job['repo'], job['stage'], output):
                        continuations.append(job)
                report = update_state(jobs, output, state, sync=sync, workers=workers)
                if (output / 'STOP').exists() or RUN_ABORT.is_set():
                    raise InterruptedError('Generation stopped; completed batches preserved')
                draining = (output / 'DRAIN').exists()
                if draining and not active:
                    write_index(repos, output)
                    print(json.dumps({'event': 'generation_worker_drained', 'completed_batches_preserved': True}), flush=True)
                    return
                refresh_due = refresh and time.monotonic() >= next_refresh
                publish_due = publish and needs_publication and time.monotonic() >= next_publication
                # 来源刷新与发布只在请求自然完成后运行，避免输入和快照在生成中改变。
                if refresh_due and not active:
                    try:
                        repos = refresh(repos)
                        jobs = reconcile_jobs(jobs, repos, output)
                        continuations.clear()
                        sync = {'checked_at': datetime.now(timezone.utc).isoformat(),
                                'state': 'ok' if all(repo.get('sync_status', 'ok') == 'ok' for repo in repos) else 'needs_attention'}
                    except Exception as exc:
                        sync = {'checked_at': datetime.now(timezone.utc).isoformat(), 'state': 'retry_wait',
                                'error': SECRET.sub('[REDACTED]', str(exc))[:500]}
                        print(json.dumps({'event': 'source_sync_retry', **sync}), flush=True)
                    next_refresh = time.monotonic() + interval
                    needs_publication = True
                    refresh_due = False
                if not active:
                    write_index(repos, output)
                    report = update_state(jobs, output, state, sync=sync, workers=workers)
                if publish and needs_publication and time.monotonic() >= next_publication and not active:
                    try:
                        publish()
                        needs_publication = False
                    except Exception as exc:
                        print(json.dumps({'event': 'publication_retry', 'error_type': type(exc).__name__}), flush=True)
                    next_publication = time.monotonic() + 30
                    publish_due = False
                if report['complete'] and not watch and not active:
                    if publish and needs_publication:
                        publish()
                    print(json.dumps({'event': 'generation_completed', 'ready': report['ready']}), flush=True)
                    return
                pending = [job for job in jobs if not ready(job['repo'], job['stage'], output)]
                if pending and not watch and not active and all(job.get('state') == 'needs_fix' for job in pending):
                    raise RuntimeError('Remaining jobs need configuration or evidence repair; see worker log')
                while len(active) < workers and not (draining or refresh_due or publish_due):
                    busy = {job['repo']['name'] for job in active.values()}
                    job = next((job for job in continuations if eligible(job, output, busy)), None)
                    if job is not None:
                        continuations.remove(job)
                    else:
                        for _ in range(len(jobs)):
                            candidate = jobs[cursor % len(jobs)]
                            cursor += 1
                            if eligible(candidate, output, busy):
                                job = candidate
                                break
                    if job is None:
                        break
                    job['state'] = 'running'
                    update_state(jobs, output, state, sync=sync, workers=workers)
                    active[pool.submit(execute_job, job, packs, output, workers)] = job
                if active:
                    wait(active, timeout=1, return_when=FIRST_COMPLETED)
                elif not (draining or refresh_due or publish_due):
                    if pending and not watch:
                        raise RuntimeError('Remaining jobs need configuration or evidence repair; see worker log')
                    time.sleep(5)
        finally:
            # 保持全局锁直到正在运行的请求收尾，禁止第二个执行器重复计费。
            pool.shutdown(wait=True)
            for future, job in active.items():
                try:
                    result = future.result()
                    job.update({key: value for key, value in result.items() if key != 'progress'})
                except InterruptedError:
                    job['state'] = 'interrupted'
            write_index(repos, output)
            update_state(jobs, output, state, sync=sync, workers=workers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--packs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--project', action='append')
    parser.add_argument('--prioritize', action='append', default=[])
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--owner')
    parser.add_argument('--repositories', type=Path)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--proxy', default='http://127.0.0.1:7897')
    parser.add_argument('--deja', type=Path)
    parser.add_argument('--history-state', type=Path)
    parser.add_argument('--codex-root', type=Path, default=Path.home() / '.codex/sessions')
    parser.add_argument('--claude-root', type=Path, default=Path.home() / '.claude/projects')
    parser.add_argument('--publish-host')
    parser.add_argument('--publication-state', type=Path)
    parser.add_argument('--publication-url', default='http://127.0.0.1:3415')
    args = parser.parse_args()
    if args.interval < 30:
        parser.error('--interval must be at least 30 seconds')
    if args.workers < 1:
        parser.error('--workers must be positive')
    if args.watch and not all([args.owner, args.repositories, args.inventory, args.deja, args.history_state]):
        parser.error('Watch mode requires GitHub directories, owner, deja and history state')
    if args.publish_host and (not args.publication_state or not os.getenv('APP_PASSWORD')):
        parser.error('Publication requires a state path and configured application access')

    def selected(repos):
        priority = args.project or args.prioritize
        repos = [repo for repo in repos if not args.project or repo['name'] in args.project]
        return sorted(repos, key=lambda repo: priority.index(repo['name']) if repo['name'] in priority else len(priority))

    def refresh(previous):
        repos = refresh_sources(args.owner, args.repositories, args.packs, args.catalog, args.inventory, args.proxy)
        export = refresh_exports(read(args.catalog), [('codex', args.codex_root), ('claude', args.claude_root)],
                                 args.deja, args.history_state, args.output, args.project)
        failed = {row['project'] for row in export['sessions'] if row.get('export', {}).get('status') == 'failed'}
        for repo in repos:
            if repo['name'] in failed:
                repo['sync_status'] = 'history_failed'
        return selected(repos)

    def publish():
        report = publish_snapshot(args.output, args.publication_state, args.publish_host,
                                  verify=lambda: verify_api(args.output, args.publication_url, os.environ['APP_PASSWORD']))
        if report['state'] == 'published':
            print(json.dumps({'event': 'knowledge_published', 'ready': report['package']['ready'],
                'topics_ready': report['package']['topics_ready'], 'history_ready': report['package']['history_ready']}), flush=True)

    run(selected(read(args.catalog)['repositories']), args.packs, args.output, args.state,
        args.watch, refresh if args.watch else None, args.interval, publish if args.publish_host else None, args.workers)


if __name__ == '__main__':
    main()
