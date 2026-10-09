import hashlib
import json

import pytest

from devrag.connector import DejaSource, export_records, normalize_line


def raw_record(text, role='user', stamp='2026-10-09T00:00:00Z'):
    return {'timestamp': stamp, 'type': 'response_item', 'payload': {'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': text}]}}


def test_codex_array_and_hidden_roles_are_separated():
    raw = {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'output': [{'type': 'text', 'text': '执行失败'}, {'type': 'image', 'data': 'not-text'}]}}
    adapted = normalize_line(raw, 'codex', 12)
    assert adapted['payload']['output'] == '[[tracebook-source:12]]\n执行失败'
    for role in ['system', 'developer']:
        assert normalize_line(raw_record('hidden', role), 'codex', 9) == raw_record('hidden', role)


def test_codex_framed_output_keeps_provenance_and_compaction_is_not_user():
    raw = {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'Chunk ID: x\nOutput:\n执行失败'}}
    adapted = normalize_line(raw, 'codex', 12)['payload']['output']
    assert '[[tracebook-source:12]]' in adapted.split('\nOutput:\n', 1)[1]
    compacted = normalize_line({'type': 'compacted', 'payload': {'message': 'AI生成的会话摘要'}}, 'codex', 19)
    assert compacted['payload']['role'] == 'assistant'
    assert '[[tracebook-source:19]]' in compacted['payload']['message']


def test_incremental_copy_preserves_original_and_defers_partial_lines(tmp_path):
    source = tmp_path / 'rollout-demo.jsonl'
    first = json.dumps(raw_record('第一段'), ensure_ascii=False).encode() + b'\n'
    second = json.dumps(raw_record('第二段'), ensure_ascii=False).encode() + b'\n'
    source.write_bytes(first + second[:40])
    adapter = DejaSource(tmp_path / 'deja.exe', tmp_path / 'state', {'harness': 'codex', 'path': str(source), 'native_id': 'demo', 'title': '会话'})
    snapshot = adapter.prepare()
    assert len(snapshot['lines']) == 1 and snapshot['pending_bytes'] == 40
    assert source.read_bytes() == first + second[:40]
    copy_before = adapter.copy.read_bytes()
    source.write_bytes(first + second)
    assert len(adapter.prepare()['lines']) == 2
    assert adapter.copy.read_bytes().startswith(copy_before)
    again = adapter.copy.read_bytes()
    adapter.prepare()
    assert adapter.copy.read_bytes() == again
    source.write_bytes(second)
    with pytest.raises(ValueError, match='改写'):
        adapter.prepare()


def test_export_has_exact_origin_secret_redaction_and_no_truncation(tmp_path):
    raw = json.dumps(raw_record('原文'), ensure_ascii=False).encode() + b'\n'
    snapshot = {'source': 'source.jsonl', 'sha256': hashlib.sha256(raw).hexdigest(),
                'lines': {1: {'sha256': hashlib.sha256(raw).hexdigest(), 'time': '2026-10-09T00:00:00Z'}}}
    content = 'x' * 26000 + ' sk-test-' + 'CredentialForTestingOnly' * 2
    messages = [{'role': 'user', 'text': '[[tracebook-source:1]]\n' + content}, {'role': 'thinking', 'text': '不索引'}]
    records = export_records(messages, snapshot, 'demo')
    joined = ''.join(item['text'] for item in records)
    assert joined.startswith('x' * 26000) and 'CredentialForTestingOnly' not in joined
    assert len(records) == 6 and all(len(item['text']) <= 5000 for item in records)
    assert {item['provenance']['record_sha256'] for item in records} == {hashlib.sha256(raw).hexdigest()}
    assert [item['provenance']['part'] for item in records] == list(range(6))
    assert export_records([{'role': 'user', 'text': '[[tracebook-source:1]]'}], snapshot, 'demo') == []
    with pytest.raises(ValueError, match='来源'):
        export_records([{'role': 'assistant', 'text': '没有可定位来源'}], snapshot, 'demo')
