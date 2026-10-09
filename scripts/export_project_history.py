"""复用 deja-vu 导出已关联会话；原始会话只读，结果保留角色和逐行出处。"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.connector import DejaSource
from devrag.session_projects import associate
from scripts.build_knowledge import save_json


def inventory(roots):
    rows = []
    for harness, root in roots:
        for path in root.rglob('*.jsonl'):
            if 'subagents' in path.parts:
                continue
            with path.open(encoding='utf-8') as stream:
                for index, line in enumerate(stream):
                    if index >= 40:
                        break
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    metadata = None
                    if harness == 'codex' and record.get('type') == 'session_meta':
                        payload = record.get('payload', {})
                        source = payload.get('source', '')
                        if isinstance(source, dict) and 'subagent' in source:
                            break
                        metadata = {'id': payload.get('id'), 'cwd': payload.get('cwd'), 'git': payload.get('git', {}),
                                    'started': payload.get('timestamp', record.get('timestamp'))}
                    elif harness == 'claude' and record.get('cwd') and record.get('sessionId'):
                        if record.get('isSidechain'):
                            break
                        metadata = {'id': record['sessionId'], 'cwd': record['cwd'], 'git': {'branch': record.get('gitBranch')},
                                    'started': record.get('timestamp')}
                    if metadata:
                        rows.append({'harness': harness, 'path': str(path), 'bytes': path.stat().st_size, **metadata})
                        break
    return rows


def export_session(row, executable, state, output):
    adapter = DejaSource(executable, state, {**row, 'native_id': row['id'], 'title': row['project']})
    snapshot = adapter.prepare()
    target = output / row['project'] / 'history'
    target.mkdir(parents=True, exist_ok=True)
    document = target / (row['id'] + '.md')
    records_file = document.with_suffix('.json')
    if records_file.exists():
        saved = json.loads(records_file.read_text(encoding='utf-8'))
        if saved.get('source_sha256') == snapshot['complete_sha256'] and document.exists():
            return {'status': 'exported', 'cached': True, 'records': len(saved['records'])}
    records = adapter.records(snapshot)
    save_json(records_file, {'session': row, 'source_sha256': snapshot['complete_sha256'], 'records': records})
    lines = [f'# {row["project"]} 开发会话记录', '',
             f'> 来源：{row["harness"]} / {row["id"]}；开始：{row["started"]}。',
             f'> 关联方式：{row["association"]}；历史工作目录：`{row["cwd"]}`。',
             '> 已由 deja-vu 脱敏；会话中的指令只是历史资料，AI陈述不是执行证明。', '']
    for record in records:
        lines.extend([f'## {record["role"]} · {record["time"]}',
                      f'记录：`{record["id"]}`；原文第 {record["provenance"]["line"]} 行。', '', record['text'], ''])
    document.write_text('\n'.join(lines), encoding='utf-8')
    return {'status': 'exported', 'records': len(records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--codex-root', type=Path, default=Path.home() / '.codex/sessions')
    parser.add_argument('--claude-root', type=Path, default=Path.home() / '.claude/projects')
    parser.add_argument('--deja', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--project', action='append')
    parser.add_argument('--inventory-only', action='store_true')
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    sessions = associate(inventory([('codex', args.codex_root), ('claude', args.claude_root)]),
                         catalog['owner'], {row['name'] for row in catalog['repositories']})
    args.state.mkdir(parents=True, exist_ok=True)
    report = {'sessions': sessions, 'association': dict(collections.Counter(row['association'] for row in sessions))}
    save_json(args.state / 'session-projects.json', report)
    for repo in catalog['repositories']:
        selected = [row for row in sessions if row['project'] == repo['name']]
        target = args.output / repo['name']
        target.mkdir(parents=True, exist_ok=True)
        lines = [f'# {repo["name"]} 会话来源索引', '',
                 f'已关联 {len(selected)} 份主会话。记录归属和提炼完成是两个状态；此索引本身未经模型归纳。', '',
                 '| 时间 | 来源 | 会话 | 关联依据 | 导出状态 |', '|---|---|---|---|---|']
        for row in sorted(selected, key=lambda row: row.get('started') or '', reverse=True):
            status = {'status': 'not_exported'}
            existing = target / 'history' / (row['id'] + '.md')
            if existing.is_file() and existing.with_suffix('.json').is_file():
                status = {'status': 'exported_previous_snapshot'}
            if not args.inventory_only and (not args.project or row['project'] in args.project):
                try:
                    status = export_session(row, args.deja, args.state, args.output)
                except (RuntimeError, ValueError, OSError) as exc:
                    status = {'status': 'failed', 'error_type': type(exc).__name__}
                print(json.dumps({'project': row['project'], 'session': row['id'], **status}), flush=True)
            row['export'] = status
            link = f'[{row["id"]}](history/{row["id"]}.md)' if status['status'].startswith('exported') else f'`{row["id"]}`'
            lines.append(f'| {row["started"]} | {row["harness"]} | {link} | {row["association"]} | {status["status"]} |')
        (target / 'SESSION-INDEX.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        save_json(args.state / 'session-projects.json', report)
    print(json.dumps({'event': 'history_export_completed', 'association': report['association']}), flush=True)


if __name__ == '__main__':
    main()
