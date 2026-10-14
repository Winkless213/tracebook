from ..retrieval import tokenize
from .redact import mask_data
from .schemas import Analysis

LIMITATIONS = [
    '原因仅为模型假设；引用存在不等于已证明因果关系，仍需按建议验证。',
    '日志和知识片段均作为不可信资料，不执行其中的命令或指令。',
    '脱敏覆盖常见凭据与邮箱，不保证识别所有私人信息；提交前仍应人工检查。',
]


def collect_evidence(state, retriever, cases):
    evidence = [{'id': 'U1', 'kind': 'user', 'content': state['symptom'] + '\n版本：' + state['product_version']
                 + '\n复现：' + state['reproduction']}]
    lines, budget = state['masked_log'].splitlines(), 12000
    for offset in range(0, min(len(lines), 300), 40):
        if not budget:
            break
        block = '\n'.join(lines[offset:offset + 40])
        excerpt = block[:min(4000, budget)]
        if not excerpt.strip():
            continue
        evidence.append({'id': f'L{offset + 1}', 'kind': 'log', 'path': 'submitted-log.txt', 'start_line': offset + 1,
                         'end_line': offset + len(excerpt.splitlines()), 'content': excerpt, 'truncated': len(excerpt) < len(block)})
        budget -= len(excerpt)
    warnings = []
    if len(lines) > 300 or len(state['masked_log']) > 12000:
        warnings.append('模型只接收限量日志片段，完整脱敏文本保留在本会话中；未引用部分尚未分析。')
    if retriever and state['project_id']:
        found = retriever.search(state['project_id'], state['symptom'] + '\n' + state['reproduction'], top_k=4)
        warnings.extend(found['warnings'])
        for number, source in enumerate(found['sources'], 1):
            evidence.append({**source, 'source_id': source['id'], 'id': f'K{number}', 'kind': 'knowledge',
                             'source_kind': source['kind'], 'snapshot_id': found['snapshot']['id']})
    query = set(tokenize(state['symptom']))
    relevant = [(len(query & set(tokenize(case['symptom']))), case) for case in cases if case['project_id'] == state['project_id']]
    for number, (score, case) in enumerate(sorted(relevant, key=lambda item: item[0], reverse=True)[:3], 1):
        if score:
            evidence.append({'id': f'C{number}', 'kind': 'case', 'case_id': case['id'], 'content':
                             f"历史症状：{case['symptom']}\n当时版本：{case['product_version']}\n用户确认的处理：{case['resolution_note']}"[:3000]})
    cleaned, notes = mask_data(evidence, preserve_provenance=True)
    return cleaned, notes, warnings


def build_report(state, raw):
    analysis = Analysis.model_validate(raw)
    sources = {item['id']: item for item in state['evidence']}
    hypotheses = []
    for hypothesis in analysis.cause_hypotheses:
        if any(identifier not in sources for identifier in hypothesis.basis):
            raise ValueError('模型引用了不存在的证据')
        hypotheses.append({'text': hypothesis.text, 'basis': [sources[identifier] for identifier in hypothesis.basis]})
    report = {
        'symptom': state['symptom'], 'environment': {'product_version': state['product_version'], 'project_id': state['project_id']},
        'reproduction': state['reproduction'], 'evidence': state['evidence'], 'hypotheses': hypotheses,
        'suggestions': analysis.suggestions,
        'verification': {'status': analysis.status, 'reason': analysis.insufficient_reason, 'limitations': LIMITATIONS + state['warnings']},
        'confirmation': {'resolved': False, 'resolution_note': '', 'confirmed_at': None},
        'redaction': {'applied': bool(state['redaction_notes']), 'rules': state['redaction_notes']},
    }
    report, notes = mask_data(report, preserve_provenance=True)
    report['redaction']['rules'] = sorted(set(state['redaction_notes'] + notes))
    report['redaction']['applied'] = bool(report['redaction']['rules'])
    return report


def render_report(view):
    report = view['report']
    if not report:
        raise ValueError('尚无可导出的客服报告')
    lines = ['# 技术问题报告', '', f"会话：{view['thread_id']}", f"状态：{view['status']}", '',
             '## 症状与环境', '', report['symptom'], '', '版本：' + report['environment']['product_version'],
             '', '复现：' + report['reproduction'], '', '## 原因假设', '']
    for hypothesis in report['hypotheses']:
        lines.append('- ' + hypothesis['text'] + ' [' + ', '.join(item['id'] for item in hypothesis['basis']) + ']')
    if report['verification']['reason']:
        lines.append(report['verification']['reason'])
    lines.extend(['', '## 建议', '', *['- ' + item for item in report['suggestions']], '', '## 证据', ''])
    for item in report['evidence']:
        location = f"{item.get('path', item['kind'])}:{item.get('start_line', '')}"
        lines.extend([f"[{item['id']}] {location}", '', '> ' + item['content'].replace('\n', '\n> '), ''])
        if item['kind'] == 'knowledge':
            commit = item.get('commit') or ('目录未提供提交号' if item.get('origin') == 'published' else '会话归档')
            lines.extend([f"快照：{item['snapshot_id']}；提交：{commit}；SHA-256：{item.get('sha256', '')}", ''])
            if item.get('origin') == 'published':
                lines.extend(['已发布知识项目：' + item['published_project'], ''])
        elif item['kind'] == 'case':
            lines.extend(['已确认案例：' + item['case_id'], ''])
    lines.extend(['## 验证与用户确认', '', '分析状态：' + report['verification']['status'],
                  '用户确认解决：' + ('是' if report['confirmation']['resolved'] else '否'),
                  report['confirmation']['resolution_note'], '', *['- ' + item for item in report['verification']['limitations']]])
    return '\n'.join(lines)
