"""为已脱敏的会话生成主题目录；归属与经验提炼分别记录。"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.distill import Distiller
from devrag.knowledge_status import history_sources
from devrag.models import SECRET
from scripts.build_knowledge import model_labels, process_batches, save_json
from scripts.history_knowledge import (
    TOPIC_SYSTEM,
    merge_topics,
    prepare_session,
    request_json,
    topic_batches,
    validate_topics,
)


def classify(repo, packs, output, one_batch=False, batch_workers=1):
    target = output / repo['name']
    sources = history_sources(target)
    sessions = [prepare_session(source) for source in sources]
    files = json.loads((packs / repo['name'] / repo['commit'] / 'code.json').read_text(encoding='utf-8'))['files']
    readme = next((text for name, text in files.items() if name.casefold() == 'readme.md'), '')
    model = Distiller()
    model.timeout = 900
    model.yield_between_batches = one_batch
    cache, results, runs = target / '.topics-cache', {}, []
    def process_batch(number, batch):
        print(json.dumps({'event': 'classifying_topics', 'project': repo['name'], 'batch': number, 'sections': len(batch)}), flush=True)
        data = {'repository': {key: repo[key] for key in ['name', 'url', 'commit']},
                'repository_readme_excerpt': SECRET.sub('[REDACTED]', readme[:10000]), 'sessions': batch}
        batch_model = model
        if batch_workers > 1:
            batch_model = Distiller()
            batch_model.timeout = model.timeout
            batch_model.yield_between_batches = one_batch
        return request_json(batch_model, data, cache, TOPIC_SYSTEM, lambda result: validate_topics(result, batch))

    inputs = topic_batches(sessions)
    # 串行仍逐批落盘；并发由现有全局信号量限流，主线程按来源顺序合并。
    completed = (process_batch(number, batch) for number, batch in enumerate(inputs, 1)) if batch_workers == 1 else (
        process_batches(process_batch, inputs, workers=batch_workers))
    for saved in completed:
        runs.append({key: saved.get(key) for key in ['input_sha256', 'model', 'effort', 'usage', 'generation_commit', 'cache_key_sha256', 'reused']})
        for row in saved['result']['sessions']:
            native_id = row['id'].rsplit('/', 1)[0]
            results.setdefault(native_id, []).append(row)
        save_topics(repo, sessions, results, runs, target)
        print(json.dumps({'event': 'topics_batch_completed', 'project': repo['name'], 'sessions_seen': len(results)}), flush=True)
    return save_topics(repo, sessions, results, runs, target)


def save_topics(repo, sessions, results, runs, target):
    rows = []
    for session in sessions:
        sections = results.get(session['id'], [])
        expected = {part['id'] for batch in topic_batches([session]) for part in batch}
        complete = {row['id'] for row in sections} == expected
        row = merge_topics(session['id'], sections) if complete else {
            'id': session['id'], 'relevance': 'pending', 'topics': [], 'sections': sections}
        rows.append({**row, 'source_sha256': session['source_sha256'], 'coverage': session['coverage'],
                     'started': session['started'], 'complete': complete})
    report = {'repository': repo['name'], 'source_commit_for_topic_context': repo['commit'],
              'all_sessions_classified': all(row['complete'] for row in rows), 'sessions': rows, 'runs': runs}
    labels = {'project': '本项目开发', 'mixed': '混合主题', 'unrelated': '其他事务', 'uncertain': '证据不足', 'pending': '待分类'}
    lines = [f'# {repo["name"]} 会话主题', '',
             f'> 分类模型：{model_labels(runs)}。',
             '> 强模型按用户请求与标明范围的助手摘录分类；不是对完整工具日志的审计，也不证明历史执行结果。',
             '> 文件目录或 Git remote 只用于关联，具体主题由实际内容判断。经验归纳见后续 SESSIONS.md。', '',
             '| 时间 | 会话 | 分类 | 主题 |', '|---|---|---|---|']
    for row in sorted(rows, key=lambda item: item['started'] or '', reverse=True):
        topics = '；'.join(row['topics']).replace('|', '/')
        lines.append(f'| {row["started"]} | [{row["id"]}](history/{row["id"]}.md) | {labels[row["relevance"]]} | {topics} |')
    if not rows:
        lines.extend(['', '当前没有已关联的会话来源，不补造项目历史。'])
    (target / 'SESSION-TOPICS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    report['markdown_sha256'] = hashlib.sha256((target / 'SESSION-TOPICS.md').read_bytes()).hexdigest()
    save_json(target / 'session-topics.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--packs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--project', action='append')
    args = parser.parse_args()
    repos = json.loads(args.catalog.read_text(encoding='utf-8'))['repositories']
    for repo in repos:
        if repo['status'] == 'verified' and (not args.project or repo['name'] in args.project):
            result = classify(repo, args.packs, args.output)
            print(json.dumps({'event': 'topics_completed', 'project': repo['name'], 'sessions': len(result['sessions'])}), flush=True)


if __name__ == '__main__':
    main()
