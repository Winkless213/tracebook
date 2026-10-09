"""会话归属只采用仓库身份和无冲突的历史目录证据。"""
import collections
import ntpath
import re


def repository_name(remote, owner):
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([\w.-]+)/([\w.-]+?)(?:\.git)?/?', remote or '')
    return match[2] if match and match[1].casefold() == owner.casefold() else None


def associate(sessions, owner, known_projects):
    paths = collections.defaultdict(set)
    for session in sessions:
        name = repository_name(session.get('git', {}).get('repository_url'), owner)
        if name in known_projects and session.get('cwd'):
            paths[ntpath.normcase(ntpath.normpath(session['cwd']))].add(name)
    result = []
    for session in sessions:
        remote = session.get('git', {}).get('repository_url')
        name = repository_name(remote, owner)
        kind = 'recorded_git_remote' if name in known_projects else None
        if not remote and session.get('cwd'):
            candidates = paths[ntpath.normcase(ntpath.normpath(session['cwd']))]
            if len(candidates) == 1:
                name, kind = next(iter(candidates)), 'same_recorded_workspace'
        result.append({**session, 'project': name if kind else None, 'association': kind or 'unmatched'})
    return result
