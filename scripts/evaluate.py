"""独立数据库评测检索；--answers 才请求真实模型，保留逐题输出。"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.llm import LLM
from devrag.models import Bundle
from devrag.retrieval import Retriever
from devrag.store import Store
from devrag.worker import Worker


def evaluate(bundle_path, output, answers=False, ids=None):
    if output.exists():
        raise SystemExit('输出已存在，保留旧结果并改用新文件')
    questions = json.loads((Path(__file__).resolve().parents[1] / 'evaluation' / 'questions.json').read_text(encoding='utf-8'))
    suite_size = len(questions)
    if ids:
        if not set(ids) <= {item['id'] for item in questions}:
            raise ValueError('题号不在冻结题集中')
        questions = [item for item in questions if item['id'] in ids]
    data = Bundle.model_validate_json(bundle_path.read_text(encoding='utf-8'))
    results = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'eval.sqlite')
        pid = store.ingest(data)['project']['id']
        retriever = Retriever(store)
        worker = Worker(store, retriever, LLM())
        for question in questions:
            modes = {}
            for baseline in [True, False]:
                started = time.monotonic()
                search = retriever.search(pid, question['question'], baseline=baseline)
                paths = {source['path'] for source in search['sources']}
                expected = set(question['paths'])
                modes[search['method']] = {'retrieval_ms': round((time.monotonic()-started)*1000), 'path_recall_at_6': len(expected & paths)/len(expected) if expected else None,
                                         'sources': search['sources']}
            row = {**question, 'retrieval': modes}
            if answers:
                job = store.create_question(pid, question['question'], question['id'])
                worker.process_next()
                result = store.question(job['id'])
                row['answer'] = result
                row['expected_status_pass'] = result.get('result', {}).get('status') == question['expected_status'] if question.get('expected_status') and result.get('result') else None
            results.append(row)
            output.write_text(json.dumps({'commit': data.commit, 'suite_size': suite_size, 'selected_ids': [item['id'] for item in questions], 'questions': results, 'complete': len(results) == len(questions)}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'id': question['id'], 'retrieval': {mode: values['path_recall_at_6'] for mode, values in modes.items()}, 'status': row.get('answer', {}).get('status')}, ensure_ascii=False), flush=True)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--answers', action='store_true')
    parser.add_argument('--ids', nargs='+')
    args = parser.parse_args()
    evaluate(args.bundle, args.output, args.answers, args.ids)
