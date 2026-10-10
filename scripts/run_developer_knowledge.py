"""串行轮转代码和会话归纳；缓存续跑，临时失败不占住整个队列。"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.knowledge_status import source_snapshots, stage_ready
from devrag.models import SECRET
from scripts.build_knowledge import RUN_ABORT, GenerationPending, RecoverableStreamError, build, save_json, write_index
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
    jobs = []
    for repo in repos:
        if repo['status'] != 'verified':
            continue
        snapshots = source_snapshots(output / repo['name'])
        for stage in ['topics', 'history', 'code']:
            version = hashlib.sha256(json.dumps({'commit': repo['commit'],
                'sources': snapshots if stage != 'code' else {}}, sort_keys=True).encode('utf-8')).hexdigest()
            old = prior.get((repo['name'], stage), {})
            job = {**old, 'repo': repo, 'stage': stage, 'version': version}
            if old.get('version') != version:
                job['state'] = 'queued'
                job.pop('error', None)
            jobs.append(job)
    return jobs


def update_state(jobs, output, state, current=None, sync=None):
    rows = []
    for job in jobs:
        row = {'project': job['repo']['name'], 'stage': job['stage'], 'state': job.get('state', 'queued')}
        if job['stage'] != 'code' and not source_snapshots(output / job['repo']['name']):
            row['state'] = 'no_sources'
        elif ready(job['repo'], job['stage'], output):
            row['state'] = 'completed'
        row.update({key: job[key] for key in ['version', 'error'] if key in job})
        rows.append(row)
    report = {'worker_pid': os.getpid(), 'updated_at': datetime.now(timezone.utc).isoformat(),
              'current': current, 'jobs': rows,
              'complete': bool(rows) and all(row['state'] in {'completed', 'no_sources'} for row in rows),
              'source_sync': sync,
              'ready': {stage: sum(row['state'] == 'completed' and row['stage'] == stage for row in rows)
                        for stage in ['code', 'topics', 'history']}}
    save_json(state, report)
    return report


def run_job(job, jobs, packs, output, state, repos=None, sync=None):
    actions = {'code': build, 'topics': classify, 'history': distill}
    repo, stage = job['repo'], job['stage']
    if (output / 'STOP').exists() or RUN_ABORT.is_set():
        raise InterruptedError('Generation stopped; completed batches preserved')
    if job.get('state') == 'needs_fix' or ready(repo, stage, output) or (stage == 'history' and not ready(repo, 'topics', output)):
        return False
    progress, job['state'] = False, 'running'
    update_state(jobs, output, state, {'project': repo['name'], 'stage': stage}, sync)
    try:
        actions[stage](repo, packs, output, one_batch=True)
        job['state'], progress = 'completed', True
    except GenerationPending:
        job['state'], progress = 'queued', True
    except RecoverableStreamError as exc:
        job['state'] = 'retry_wait'
        print(json.dumps({'event': 'job_yielded_for_retry', 'project': repo['name'], 'stage': stage,
                          'error_type': type(exc.__cause__).__name__}), flush=True)
    except InterruptedError:
        job['state'] = 'interrupted'
        update_state(jobs, output, state, sync=sync)
        raise
    except Exception as exc:
        job['state'], job['error'] = 'needs_fix', SECRET.sub('[REDACTED]', str(exc))[:500]
        print(json.dumps({'event': 'job_needs_fix', 'project': repo['name'], 'stage': stage,
                          'error_type': type(exc).__name__, 'error': job['error']}), flush=True)
    write_index(repos if repos is not None else [job['repo'] for job in jobs if job['stage'] == 'code'], output)
    update_state(jobs, output, state, sync=sync)
    return progress


def run_pass(jobs, packs, output, state):
    progress = False
    for job in jobs:
        progress = run_job(job, jobs, packs, output, state) or progress
    return progress


def run(repos, packs, output, state, watch=False, refresh=None, interval=300, publish=None):
    output.mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    jobs = reconcile_jobs(read(state).get('jobs', []), repos, output)
    cursor, next_refresh, next_publication, sync = 0, 0, 0, None
    RUN_ABORT.clear()
    with FileLock(output / 'generation-run.lock', timeout=0):
        write_index(repos, output)
        print(json.dumps({'event': 'generation_worker_started', 'pid': os.getpid(), 'projects': len(repos)}), flush=True)
        try:
            while True:
                if (output / 'STOP').exists() or RUN_ABORT.is_set():
                    raise InterruptedError('Generation stopped; completed batches preserved')
                if refresh and time.monotonic() >= next_refresh:
                    try:
                        repos = refresh(repos)
                        jobs = reconcile_jobs(jobs, repos, output)
                        sync = {'checked_at': datetime.now(timezone.utc).isoformat(),
                                'state': 'ok' if all(repo.get('sync_status', 'ok') == 'ok' for repo in repos) else 'needs_attention'}
                    except Exception as exc:
                        sync = {'checked_at': datetime.now(timezone.utc).isoformat(), 'state': 'retry_wait',
                                'error': SECRET.sub('[REDACTED]', str(exc))[:500]}
                        print(json.dumps({'event': 'source_sync_retry', **sync}), flush=True)
                    next_refresh = time.monotonic() + interval
                    write_index(repos, output)
                report = update_state(jobs, output, state, sync=sync)
                if publish and time.monotonic() >= next_publication:
                    try:
                        publish()
                    except Exception as exc:
                        print(json.dumps({'event': 'publication_retry', 'error_type': type(exc).__name__}), flush=True)
                    next_publication = time.monotonic() + 30
                if report['complete'] and not watch:
                    print(json.dumps({'event': 'generation_completed', 'ready': report['ready']}), flush=True)
                    return
                pending = [job for job in jobs if not ready(job['repo'], job['stage'], output)]
                if pending and not watch and all(job.get('state') == 'needs_fix' for job in pending):
                    raise RuntimeError('Remaining jobs need configuration or evidence repair; see worker log')
                for _ in range(len(jobs)):
                    job = jobs[cursor % len(jobs)]
                    cursor += 1
                    if job.get('state') != 'needs_fix' and not ready(job['repo'], job['stage'], output) and (
                            job['stage'] != 'history' or ready(job['repo'], 'topics', output)):
                        if run_job(job, jobs, packs, output, state, repos, sync) and not ready(job['repo'], job['stage'], output):
                            # 成功批次继续完成当前阶段；临时失败仍让出队列。
                            cursor -= 1
                        break
                else:
                    if pending and not watch:
                        raise RuntimeError('Remaining jobs need configuration or evidence repair; see worker log')
                    time.sleep(5)
        finally:
            update_state(jobs, output, state)


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
        args.watch, refresh if args.watch else None, args.interval, publish if args.publish_host else None)


if __name__ == '__main__':
    main()
