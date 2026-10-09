"""组合 GitHub、Git、Repomix 和已有归纳器；只操作独立源码副本。"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_knowledge import RUN_ABORT, build, save_json, write_index

GIT_ENV = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never',
           'GCM_GUI_PROMPT': 'false', 'GIT_LFS_SKIP_SMUDGE': '1', 'GIT_OPTIONAL_LOCKS': '0'}


def git(path, args, proxy=None):
    command = ['git'] + (['-C', str(path)] if path else [])
    if proxy:
        command += ['-c', 'http.proxy=' + proxy]
    result = subprocess.run([*command, *args], capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=600, env=GIT_ENV,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(f'git {args[0]} failed (exit {result.returncode})')
    return result.stdout.strip()


def discover(owner, proxy):
    credential = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                                text=True, encoding='utf-8', capture_output=True, timeout=30, env=GIT_ENV,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    values = dict(line.split('=', 1) for line in credential.stdout.splitlines() if '=' in line)
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'User-Agent': 'Tracebook-knowledge-sync'}
    if credential.returncode or not values.get('password'):
        raise RuntimeError('GitHub authentication is required to include private repositories')
    headers['Authorization'] = 'Bearer ' + values['password']
    rows = []
    with httpx.Client(base_url='https://api.github.com', headers=headers, proxy=proxy,
                      timeout=httpx.Timeout(45, connect=15), trust_env=False) as client:
        response = client.get('/user')
        response.raise_for_status()
        identity = response.json()['login']
        page = 1
        while True:
            response = client.get('/user/repos', params={'affiliation': 'owner,collaborator,organization_member',
                'visibility': 'all', 'sort': 'full_name', 'per_page': 100, 'page': page})
            response.raise_for_status()
            for row in response.json():
                if row['owner']['login'].casefold() == owner.casefold():
                    rows.append({key: row[key] for key in ['name', 'default_branch', 'private', 'archived', 'html_url', 'pushed_at']})
            if 'next' not in response.links:
                break
            page += 1
    return {'owner': owner, 'authenticated_as': identity, 'listed_at': datetime.now(timezone.utc).isoformat(),
            'repositories': sorted(rows, key=lambda row: row['name'].casefold())}


def checkout(repo, owner, root, proxy):
    name = repo['name']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in {'.', '..'}:
        raise ValueError('Invalid repository name')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Repository path outside source directory')
    url = f'https://github.com/{owner}/{name}.git'
    result = {'name': name, 'path': str(path), 'url': url, 'branch': repo['default_branch']}
    try:
        if path.exists():
            if git(path, ['status', '--porcelain'], proxy):
                raise RuntimeError('Repository has local changes; preserved')
            if git(path, ['remote', 'get-url', 'origin'], proxy) != url:
                raise RuntimeError('Repository has a different origin; preserved')
            if Path(git(path, ['rev-parse', '--show-toplevel'], proxy)).resolve() != path:
                raise RuntimeError('Repository root differs; preserved')
            if git(path, ['branch', '--show-current'], proxy) != result['branch']:
                raise RuntimeError('Default branch changed; explicit reconciliation required')
            git(path, ['fetch', '--no-tags', 'origin', result['branch']], proxy)
            git(path, ['merge', '--ff-only', 'FETCH_HEAD'], proxy)
        else:
            root.mkdir(parents=True, exist_ok=True)
            git(None, ['clone', '--quiet', '--depth=1', '--single-branch', '--no-tags',
                       '--branch', result['branch'], url, str(path)], proxy)
        commit = git(path, ['rev-parse', 'HEAD'], proxy)
        remote = git(path, ['ls-remote', '--exit-code', 'origin', 'refs/heads/' + result['branch']], proxy)
        if remote.split()[0] != commit or git(path, ['status', '--porcelain'], proxy):
            raise RuntimeError('Checkout differs from remote default branch')
        git(path, ['fsck', '--connectivity-only', '--no-reflogs'], proxy)
        files = [name for name in git(path, ['ls-files', '-z'], proxy).split('\0') if name]
        result.update(status='verified', commit=commit, tracked_files=len(files), files=files,
                      checked_at=datetime.now(timezone.utc).isoformat())
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        result.update(status='failed', error=str(exc))
    return result


def refresh_sources(owner, repositories, packs, catalog, inventory_path, proxy=None):
    inventory = discover(owner, proxy)
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    save_json(inventory_path, inventory)
    previous = json.loads(catalog.read_text(encoding='utf-8'))['repositories'] if catalog.is_file() else []
    prior = {row['name']: row for row in previous}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        current = list(pool.map(lambda row: checkout(row, owner, repositories, proxy), inventory['repositories']))
    repos, checked = [], datetime.now(timezone.utc).isoformat()
    for row in current:
        if row['status'] == 'failed' and prior.get(row['name'], {}).get('status') == 'verified':
            row = {**prior[row['name']], 'sync_status': 'failed', 'sync_error': row['error'], 'sync_checked_at': checked}
        else:
            row = {**row, 'sync_status': 'ok' if row['status'] == 'verified' else 'failed', 'sync_checked_at': checked}
        repos.append(row)
    found = {row['name'] for row in repos}
    repos.extend({**row, 'sync_status': 'not_listed', 'sync_checked_at': checked}
                 for row in previous if row['name'] not in found)
    save_json(catalog, {**inventory, 'repositories': repos})
    subprocess.run(['node', str(Path(__file__).parent / 'knowledge/pack-projects.mjs'), str(catalog), str(packs)],
                   check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return repos


def cycle(args):
    args.output.mkdir(parents=True, exist_ok=True)
    with FileLock(args.output / 'generation-run.lock', timeout=0):
        return generation_cycle(args)


def generation_cycle(args):
    RUN_ABORT.clear()
    repos = refresh_sources(args.owner, args.state / 'repositories', args.state / 'packs',
                            args.state / 'github-checkouts.json', args.state / 'github-repositories.json', args.proxy)
    write_index(repos, args.output)
    published = json.loads((args.output / 'catalog.json').read_text(encoding='utf-8'))['projects']
    ready = {row['name'] for row in published if row['status'] == 'ready'}
    failures_file = args.state / 'generation-failures.json'
    failures = json.loads(failures_file.read_text(encoding='utf-8')) if failures_file.exists() else {}
    pending = [row for row in repos if row['status'] == 'verified' and row['name'] not in ready
               and (args.retry_failed or failures.get(row['name'], {}).get('commit') != row['commit'])]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(build, row, args.state / 'packs', args.output): row for row in pending}
        for job in concurrent.futures.as_completed(jobs):
            row = jobs[job]
            try:
                job.result()
                failures.pop(row['name'], None)
            except Exception as exc:
                failures[row['name']] = {'commit': row['commit'], 'error_type': type(exc).__name__}
            save_json(failures_file, failures)
            write_index(repos, args.output)
    print(json.dumps({'event': 'cycle_completed', 'repositories': len(repos), 'generation_failures': list(failures)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--proxy')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9-]+', args.owner):
        parser.error('Invalid GitHub owner')
    args.state.mkdir(parents=True, exist_ok=True)
    with FileLock(args.state / 'sync.lock', timeout=0):
        while True:
            cycle(args)
            if not args.watch:
                break
            args.retry_failed = False
            time.sleep(300)


if __name__ == '__main__':
    main()
