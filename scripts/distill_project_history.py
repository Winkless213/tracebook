"""将已分类会话的具体证据与当前源码关联，生成供开发助手读取的经验 Markdown。"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

from rank_bm25 import BM25Okapi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.distill import Distiller
from devrag.indexing import split_file
from devrag.knowledge_status import history_sources
from devrag.models import SECRET
from devrag.retrieval import tokenize
from scripts.build_knowledge import model_labels, process_batches, save_json
from scripts.history_knowledge import LESSON_SYSTEM, request_json, validate_lessons


def label_delegated_records(records):
    groups = collections.defaultdict(list)
    for row in records:
        source = row['provenance']
        groups[(source['file'], source['line'], row['role'])].append(row)
    delegated = set()
    for (_, _, role), parts in groups.items():
        text = ''.join(row['text'] for row in sorted(parts, key=lambda row: row['provenance']['part']))
        if role == 'user' and text.lstrip().startswith('<codex_delegation>'):
            delegated.update(row['id'] for row in parts)
    # 委派消息使用 user 信封，但不能充当人类直接决定；保留原始正文和信封角色。
    return [{**row, 'role': 'agent-delegation', 'source_role': row['role'],
             'evidence_status': 'unverified_agent_dispatch'} if row['id'] in delegated else row for row in records]


def selected_records(sources, topics):
    selected, coverage = [], []
    classifications = {row['id']: row for row in topics['sessions']}
    for source in sources:
        session = source['session']
        topic = classifications[session['id']]
        if not topic['complete'] or topic['source_sha256'] != source['source_sha256']:
            raise ValueError('Topic classification is incomplete or stale')
        records = label_delegated_records(source['records'])
        ids = {rid for section in topic['sections'] if section['relevance'] in {'project', 'mixed'}
               for rid in section['evidence_ids']}
        positions = {index for index, row in enumerate(records) if row['id'] in ids}
        surrounding = {near for index in positions for near in range(max(0, index - 2), min(len(records), index + 3))}
        for index in sorted(surrounding):
            row = records[index]
            selected.append({**row, 'session_id': session['id'], 'topics': topic['topics'],
                             'selection': 'topic_evidence' if row['id'] in ids else 'neighboring_context'})
        coverage.append({'session': session['id'], 'classification': topic['relevance'],
                         'total_records': len(records), 'selected_records': len(surrounding),
                         'source_sha256': source['source_sha256']})
    return selected, coverage


class CodeEvidence:
    def __init__(self, files):
        self.chunks, self.skipped = [], []
        for path, content in files.items():
            try:
                self.chunks.extend(split_file(path, SECRET.sub('[REDACTED]', content)))
            except ValueError:
                self.skipped.append(path)
        self.terms = [tokenize(row['path'] + '\n' + row['symbol'] + '\n' + row['content']) for row in self.chunks]
        self.index = BM25Okapi(self.terms) if any(self.terms) else None

    def retrieve(self, records):
        query = tokenize('\n'.join(' '.join(row['topics']) + '\n' + row['text'] for row in records))
        if not query or self.index is None:
            return {}
        words, result = set(query), collections.defaultdict(list)
        ranked = sorted(enumerate(self.index.get_scores(query)), key=lambda item: item[1], reverse=True)
        for index, score in ranked:
            chunk = self.chunks[index]
            if not words.intersection(self.terms[index]) or len(result[chunk['path']]) >= 2:
                continue
            result[chunk['path']].append(chunk['content'])
            if sum(map(len, result.values())) >= 12:
                break
        return dict(result)


def batches(records, characters=95000):
    batch, size = [], 0
    for row in records:
        length = len(json.dumps(row, ensure_ascii=False))
        if batch and size + length > characters:
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += length
    if batch:
        yield batch


def distill(repo, packs, output, one_batch=False, batch_workers=1):
    target = output / repo['name']
    topics = json.loads((target / 'session-topics.json').read_text(encoding='utf-8'))
    if not topics['all_sessions_classified']:
        raise ValueError('Classify all source sessions before distillation')
    sources = history_sources(target)
    records, coverage = selected_records(sources, topics)
    files = json.loads((packs / repo['name'] / repo['commit'] / 'code.json').read_text(encoding='utf-8'))['files']
    index, model, results, runs = CodeEvidence(files), Distiller(), [], []
    # 与代码归纳一致，避免强推理被旧十五分钟时限截断，并保留更长配置。
    model.timeout = max(model.timeout, 3600)
    model.yield_between_batches = one_batch
    def process_batch(number, batch):
        snippets = index.retrieve(batch)
        data = {'repository': {key: repo[key] for key in ['name', 'url', 'commit']},
                'scope': '主题分类选中的历史记录与相邻上下文；不是全部会话全文。源码仅含按主题检索的静态片段。',
                'records': batch, 'code': snippets}
        print(json.dumps({'event': 'distilling_history', 'project': repo['name'], 'batch': number, 'records': len(batch)}), flush=True)
        batch_model = model
        if batch_workers > 1:
            batch_model = Distiller()
            batch_model.timeout = model.timeout
            batch_model.yield_between_batches = one_batch
        return request_json(batch_model, data, target / '.lessons-cache', LESSON_SYSTEM,
                            lambda result: validate_lessons(result, batch, snippets))

    for saved in process_batches(process_batch, batches(records), workers=batch_workers):
        results.append(saved['result'])
        runs.append({key: saved.get(key) for key in ['input_sha256', 'model', 'effort', 'usage', 'generation_commit', 'cache_key_sha256', 'reused']})
    return publish(repo, records, coverage, results, runs, index.skipped, target)


def publish(repo, records, coverage, results, runs, skipped, target):
    mapping = {row['id']: row for row in records}
    items = list({json.dumps(row, sort_keys=True): row for result in results for row in result['items']}.values())
    report = {'repository': repo['name'], 'commit': repo['commit'], 'items': items,
              'gaps': list(dict.fromkeys(gap for result in results for gap in result['gaps'])),
              'coverage': coverage, 'runs': runs, 'code_files_not_in_retrieval': skipped,
              'all_selected_batches_completed': True}
    save_json(target / 'history-knowledge.json', report)
    labels = {'user_decision': '历史用户决定', 'candidate': '候选经验', 'open_question': '未决问题'}
    lines = [f'# {repo["name"]} 开发会话知识', '',
             f'> 关联代码提交：`{repo["commit"]}`。{model_labels(runs)} 按主题归纳，历史状态不等于当前运行状态。',
             f'> 已分类 {len(coverage)} 份来源，选取 {len(records)} 条话题证据及相邻记录；未将完整工具日志全部送入模型。',
             '> 会话与源码引文已逐字校验；这不等于语义审计或测试通过。改代码前打开对应源码。',
             '> 主题范围与排除结果见 [会话主题目录](SESSION-TOPICS.md)。', '']
    for number, item in enumerate(items, 1):
        lines.extend([f'## {number}. {item["title"]}', '', f'类型：{labels[item["kind"]]}。', '', item['detail'], '',
                      '适用边界：' + item['scope'], ''])
        for evidence in item['evidence']:
            row = mapping[evidence['record_id']]
            lines.extend([f'- 历史依据：[{row["role"]} · {row["time"]} · 原文 L{row["provenance"]["line"]}]'
                          f'(history/{row["session_id"]}.md)，记录 `{row["id"]}`。',
                          '  引文：' + evidence['quote'].replace('\n', ' / ')])
        for reference in item['code']:
            link = repo['url'].removesuffix('.git') + '/blob/' + repo['commit'] + '/' + quote(reference['path'], safe='/')
            lines.append(f'- 当前源码：[{reference["path"]}]({link})；引文：`' + reference['quote'].replace('`', "'").replace('\n', ' / ') + '`')
        if not item['code']:
            lines.append('- 当前源码关联尚未确认；只作历史线索。')
        lines.append('')
    if not items:
        lines.extend(['当前选定证据没有形成可发布的开发经验；不补造结论。', ''])
    if report['gaps']:
        lines.extend(['## 未确认事项', '', *['- ' + gap for gap in report['gaps']], ''])
    document = target / 'SESSIONS.md'
    document.write_text('\n'.join(lines), encoding='utf-8')
    save_json(target / 'history-provenance.json', {'commit': repo['commit'], 'all_selected_batches_completed': True,
        'sessions': len(coverage), 'selected_records': len(records), 'items': len(items),
        'source_snapshots': {row['session']: row['source_sha256'] for row in coverage},
        'sessions_sha256': hashlib.sha256(document.read_bytes()).hexdigest(),
        'topics_sha256': hashlib.sha256((target / 'SESSION-TOPICS.md').read_bytes()).hexdigest(), 'model_runs': runs})
    print(json.dumps({'event': 'history_knowledge_completed', 'project': repo['name'], 'items': len(items), 'sessions': len(coverage)}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--packs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--project', action='append')
    args = parser.parse_args()
    for repo in json.loads(args.catalog.read_text(encoding='utf-8'))['repositories']:
        if repo['status'] == 'verified' and (not args.project or repo['name'] in args.project):
            distill(repo, args.packs, args.output)


if __name__ == '__main__':
    main()
