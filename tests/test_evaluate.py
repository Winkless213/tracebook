import hashlib
import json

import pytest

from scripts.evaluate import check_questions, load_bundles, score, sign_test, summarize


def source(path, start, end):
    return {'path': path, 'start_line': start, 'end_line': end}


def test_hit_needs_a_source_overlapping_the_answer_lines():
    question = {'category': 'B', 'evidence': [{'path': 'a.py', 'start': 10, 'end': 12, 'quote': 'x'}]}
    assert score(question, [source('a.py', 1, 9), source('a.py', 11, 30)]) == {'hit': True, 'first_rank': 2}
    assert score(question, [source('a.py', 13, 40), source('b.py', 10, 12)]) == {'hit': False, 'first_rank': None}


def test_comparison_question_needs_both_document_and_code_lines():
    question = {'category': 'C', 'evidence': [{'path': 'README.md', 'start': 3, 'end': 3, 'quote': 'x'},
                                              {'path': 'app.py', 'start': 5, 'end': 8, 'quote': 'y'}]}
    assert score(question, [source('README.md', 1, 9)]) == {'hit': False, 'first_rank': 1}
    assert score(question, [source('app.py', 1, 6), source('README.md', 1, 9)]) == {'hit': True, 'first_rank': 1}


def test_check_questions_rejects_a_quote_outside_its_lines():
    files = {'demo': {'a.py': 'one\ntwo\nthree\n'}}
    good = [{'id': 'd1', 'repo': 'demo', 'category': 'A', 'evidence': [{'path': 'a.py', 'start': 2, 'end': 3, 'quote': 'three'}]}]
    check_questions(good, files)
    with pytest.raises(ValueError, match='d1'):
        check_questions([{**good[0], 'evidence': [{'path': 'a.py', 'start': 1, 'end': 1, 'quote': 'three'}]}], files)


@pytest.mark.parametrize('change', [
    {'repo': 'other'},
    {'evidence': [{'path': 'b.py', 'start': 1, 'end': 1, 'quote': 'one'}]},
    {'evidence': [{'path': 'a.py', 'start': 3, 'end': 4, 'quote': 'three'}]},
    {'evidence': [{'path': 'long.py', 'start': 1, 'end': 41, 'quote': 'x'}]},
    {'evidence': [{'path': 'tests/test_a.py', 'start': 1, 'end': 1, 'quote': 'one'}]},
    {'category': 'C'},
    {'category': 'B', 'question': '第三行是 three 吗'},
    {'category': 'D', 'evidence': [], 'expected_status': 'insufficient', 'absence_check': ['飞书', 'TWO']},
    {'category': 'D', 'expected_status': 'insufficient', 'absence_check': ['飞书']},
])
def test_check_questions_enforces_the_frozen_rules(change):
    files = {'demo': {'a.py': 'one\ntwo\nthree\n', 'long.py': 'x\n' * 41, 'tests/test_a.py': 'one\n'}}
    question = {'id': 'd1', 'repo': 'demo', 'category': 'A', 'question': '第三行是什么', 'evidence': [{'path': 'a.py', 'start': 2, 'end': 3, 'quote': 'three'}]}
    check_questions([question], files)
    with pytest.raises(ValueError, match='d1'):
        check_questions([{**question, **change}], files)


def test_check_questions_rejects_duplicate_ids():
    question = {'id': 'd1', 'repo': 'demo', 'category': 'A', 'evidence': [{'path': 'a.py', 'start': 1, 'end': 1, 'quote': 'one'}]}
    with pytest.raises(ValueError, match='d1'):
        check_questions([question, question], {'demo': {'a.py': 'one\n'}})


def test_summary_counts_hits_rank_and_paired_wins_against_bm25():
    def run(hit, rank, ms):
        return {'hit': hit, 'first_rank': rank, 'retrieval_ms': ms}
    rows = [{'id': 'a1', 'category': 'A', 'retrieval': {'bm25-symbol': run(True, 1, 10), 'term-overlap': run(False, None, 4)}},
            {'id': 'b1', 'category': 'B', 'retrieval': {'bm25-symbol': run(False, None, 30), 'term-overlap': run(True, 2, 6)}},
            {'id': 'c1', 'category': 'C', 'retrieval': {'bm25-symbol': run(False, 4, 20), 'term-overlap': run(False, None, 2)}},
            {'id': 'd1', 'category': 'D', 'retrieval': {}}]
    bm25 = summarize(rows, 'bm25-symbol')
    assert bm25['all'] == {'n': 3, 'hit_at_6': 0.3333, 'mrr': 0.4167}
    assert bm25['by_category'] == {'A': {'n': 1, 'hit_at_6': 1.0, 'mrr': 1.0}, 'B': {'n': 1, 'hit_at_6': 0.0, 'mrr': 0.0},
                                   'C': {'n': 1, 'hit_at_6': 0.0, 'mrr': 0.25}}
    assert bm25['median_ms'] == 20
    assert 'paired' not in bm25
    assert summarize(rows, 'term-overlap')['paired'] == {'against': 'bm25-symbol', 'wins': ['b1'], 'losses': ['a1'], 'p': 1.0}


def test_sign_test_is_two_sided_and_exact():
    assert sign_test(0, 0) == 1.0
    assert sign_test(6, 0) == pytest.approx(1 / 32)
    assert sign_test(2, 5) == sign_test(5, 2) == pytest.approx(2 * (1 + 7 + 21) / 128)


def test_load_bundles_checks_the_pinned_commit_and_drops_excluded_paths(tmp_path):
    def file(path, content):
        return {'path': path, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}
    bundle = {'schema': 1, 'project': 'demo', 'branch': 'a' * 40, 'commit': 'a' * 40, 'exported_at': '2026-11-03T00:00:00+00:00',
              'files': [file('a.py', 'x = 1\n'), file('evaluation/questions.json', '[]\n')]}
    (tmp_path / 'demo.json').write_text(json.dumps(bundle), encoding='utf-8')
    loaded = load_bundles(tmp_path, {'demo': {'commit': 'a' * 40, 'exclude': ['evaluation/']}})
    assert [item.path for item in loaded['demo'].files] == ['a.py']
    with pytest.raises(ValueError, match='demo'):
        load_bundles(tmp_path, {'demo': {'commit': 'b' * 40, 'exclude': []}})
