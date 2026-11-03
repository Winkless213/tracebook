"""在 corpora.json 固定的提交上评测检索，按证据行判分；--answers 才请求真实模型，保留逐题输出。规则见 evaluation/README.md。"""
import argparse
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.indexing import kind_for
from devrag.llm import LLM
from devrag.models import Bundle
from devrag.retrieval import METHODS, VECTOR_METHODS, Retriever
from devrag.semantic import LocalModels
from devrag.store import Store
from devrag.worker import Worker

EVALUATION = Path(__file__).resolve().parents[1] / 'evaluation'
REFERENCE = 'bm25-symbol'


def overlaps(source, item):
    return source['path'] == item['path'] and source['start_line'] <= item['end'] and source['end_line'] >= item['start']


def score(question, sources):
    """A/B 命中任一条证据即可，C 要文档和代码证据各命中一条；first_rank 是第一个与任一证据相交的片段名次。"""
    evidence = question['evidence']
    ranks = [rank for rank, source in enumerate(sources, 1) if any(overlaps(source, item) for item in evidence)]
    if question['category'] == 'C':
        groups = [[item for item in evidence if item['path'].endswith('.md') == document] for document in (True, False)]
        hit = all(any(overlaps(source, item) for source in sources for item in group) for group in groups)
    else:
        hit = bool(ranks)
    return {'hit': hit, 'first_rank': ranks[0] if ranks else None}


def problem(question, corpus):
    evidence = question['evidence']
    if question['category'] == 'D':
        if evidence or question.get('expected_status') != 'insufficient':
            return 'D 类题不能有证据，expected_status 必须是 insufficient'
        found = [term for term in question.get('absence_check', []) if any(term.lower() in text.lower() for text in corpus.values())]
        return f'语料里出现了缺席词 {found}' if found else None
    if not evidence:
        return '缺少证据'
    for item in evidence:
        where = f"{item['path']}:{item['start']}-{item['end']}"
        if item['path'] not in corpus:
            return f'语料里没有 {item["path"]}'
        if kind_for(item['path']) == 'test':
            return f'证据不能取测试文件 {where}'
        lines = corpus[item['path']].splitlines()
        if not 1 <= item['start'] <= item['end'] <= len(lines) or item['end'] - item['start'] >= 40:
            return f'行号越界或超过 40 行 {where}'
        if item['quote'] not in '\n'.join(lines[item['start'] - 1:item['end']]):
            return f'引文不在 {where} 内'
    if question['category'] == 'C' and {item['path'].endswith('.md') for item in evidence} != {True, False}:
        return 'C 类题要同时有文档和代码证据'
    if question['category'] == 'B' and re.search('[A-Za-z]', question['question']):
        return 'B 类题不能含 ASCII 字母'
    return None


def check_questions(questions, files):
    """加载题集时逐题核对；files 是 {仓库: {路径: 内容}}，不符就报出题号。"""
    seen = set()
    for question in questions:
        if question['id'] in seen:
            raise ValueError(f"{question['id']}: 题号重复")
        seen.add(question['id'])
        if question['repo'] not in files:
            raise ValueError(f"{question['id']}: 语料里没有仓库 {question['repo']}")
        reason = problem(question, files[question['repo']])
        if reason:
            raise ValueError(f"{question['id']}: {reason}")


def load_bundles(directory, corpora):
    """读取各仓库快照，核对 corpora.json 固定的提交，去掉排除的目录。"""
    bundles = {}
    for repo, corpus in corpora.items():
        data = json.loads((directory / f'{repo}.json').read_text(encoding='utf-8'))
        if data['commit'] != corpus['commit']:
            raise ValueError(f"{repo} 的快照提交 {data['commit'][:7]} 不是 corpora.json 固定的 {corpus['commit'][:7]}")
        data['files'] = [item for item in data['files'] if not item['path'].startswith(tuple(corpus['exclude']))]
        bundles[repo] = Bundle.model_validate(data)
    return bundles


def sign_test(wins, losses):
    """双侧精确符号检验，只计胜负题。"""
    total = wins + losses
    if not total:
        return 1.0
    return min(1.0, 2 * sum(math.comb(total, k) for k in range(min(wins, losses) + 1)) / 2 ** total)


def rates(results):
    if not results:
        return {'n': 0, 'hit_at_6': None, 'mrr': None}
    return {'n': len(results), 'hit_at_6': round(sum(item['hit'] for item in results) / len(results), 4),
            'mrr': round(sum(1 / item['first_rank'] if item['first_rank'] else 0 for item in results) / len(results), 4)}


def summarize(rows, method):
    """只统计 A/B/C；D 类不进检索指标。非参照方法附带与 bm25-symbol 的逐题胜负。"""
    rows = [row for row in rows if row['category'] != 'D' and method in row['retrieval']]
    summary = {'all': rates([row['retrieval'][method] for row in rows]),
               'by_category': {category: rates([row['retrieval'][method] for row in rows if row['category'] == category])
                               for category in sorted({row['category'] for row in rows})},
               'median_ms': statistics.median(row['retrieval'][method]['retrieval_ms'] for row in rows) if rows else None}
    if method != REFERENCE:
        pairs = [row for row in rows if REFERENCE in row['retrieval']]
        wins = [row['id'] for row in pairs if row['retrieval'][method]['hit'] and not row['retrieval'][REFERENCE]['hit']]
        losses = [row['id'] for row in pairs if row['retrieval'][REFERENCE]['hit'] and not row['retrieval'][method]['hit']]
        summary['paired'] = {'against': REFERENCE, 'wins': wins, 'losses': losses, 'p': round(sign_test(len(wins), len(losses)), 4)}
    return summary


def table(summary):
    lines = []
    for method, values in summary.items():
        parts = [f"{method:<14} hit@6 {values['all']['hit_at_6']}", f"MRR {values['all']['mrr']}",
                 ' '.join(f"{category} {item['hit_at_6']}" for category, item in values['by_category'].items()), f"中位 {values['median_ms']}ms"]
        if 'paired' in values:
            paired = values['paired']
            parts.append(f"对 {paired['against']} 胜 {len(paired['wins'])} 负 {len(paired['losses'])} p={paired['p']}")
        lines.append(' | '.join(parts))
    return '\n'.join(lines)


def evaluate(bundle_dir, output, answers=False, ids=None, methods=METHODS, models_dir=None):
    if output.exists():
        raise SystemExit('输出已存在，保留旧结果并改用新文件')
    questions = json.loads((EVALUATION / 'questions.json').read_text(encoding='utf-8'))
    bundles = load_bundles(bundle_dir, json.loads((EVALUATION / 'corpora.json').read_text(encoding='utf-8')))
    check_questions(questions, {repo: {item.path: item.content for item in bundle.files} for repo, bundle in bundles.items()})
    suite_size = len(questions)
    if ids:
        if not set(ids) <= {item['id'] for item in questions}:
            raise ValueError('题号不在冻结题集中')
        questions = [item for item in questions if item['id'] in ids]
    rows = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'eval.sqlite')
        projects = {repo: store.ingest(bundle)['project']['id'] for repo, bundle in bundles.items()}
        retriever = Retriever(store, models=LocalModels(models_dir) if VECTOR_METHODS & set(methods) else None)
        worker = Worker(store, retriever, LLM()) if answers else None
        for question in questions:
            pid = projects[question['repo']]
            modes = {}
            if question['category'] != 'D':
                for method in methods:
                    started = time.monotonic()
                    search = retriever.search(pid, question['question'], method=method)
                    sources = [{key: source[key] for key in ('path', 'start_line', 'end_line', 'score')} for source in search['sources']]
                    modes[method] = {**score(question, sources), 'retrieval_ms': round((time.monotonic() - started) * 1000), 'sources': sources}
            row = {**question, 'retrieval': modes}
            if worker:
                job = store.create_question(pid, question['question'], question['id'])
                worker.process_next()
                result = store.question(job['id'])
                row['answer'] = result
                row['expected_status_pass'] = result.get('result', {}).get('status') == question['expected_status'] if question.get('expected_status') and result.get('result') else None
            rows.append(row)
            report = {'commits': {repo: bundle.commit for repo, bundle in bundles.items()}, 'suite_size': suite_size, 'selected_ids': [item['id'] for item in questions],
                      'methods': list(methods), 'summary': {method: summarize(rows, method) for method in methods}, 'questions': rows, 'complete': len(rows) == len(questions)}
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'id': question['id'], **{method: values['hit'] for method, values in modes.items()}, 'status': row.get('answer', {}).get('status')},
                             ensure_ascii=False), flush=True)
    if rows:
        print(table(report['summary']))
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundles', type=Path, required=True, help='按 corpora.json 导出的快照目录，每个仓库一份 <仓库名>.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--methods', nargs='+', choices=METHODS, default=list(METHODS))
    parser.add_argument('--models-dir', type=Path, default=os.getenv('FASTEMBED_CACHE_DIR'), help='fastembed 模型缓存目录，向量类方法才用到')
    parser.add_argument('--answers', action='store_true')
    parser.add_argument('--ids', nargs='+')
    args = parser.parse_args()
    evaluate(args.bundles, args.output, args.answers, args.ids, args.methods, args.models_dir)
