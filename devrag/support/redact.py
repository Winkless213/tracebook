import re

PLACEHOLDER = '[已脱敏]'

# 顺序有意义：先整块吞掉私钥与 Authorization 头，再处理零散凭据，避免半截残留。
RULES = [
    ('private_key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)')),
    ('authorization', re.compile(
        r'''(?i)\b((?:proxy-)?authorization["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\r\n,}]+)''')),
    ('cookie', re.compile(r'(?i)\b(set-cookie|cookie)\s*[:=]\s*[^\r\n]+')),
    ('credential_assignment', re.compile(
        r'(?i)\b(api[_-]?key|apikey|secret|password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|client[_-]?secret|session[_-]?id)\b'
        r'''(["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;"'&}]+)''')),
    ('url_credentials', re.compile(r'(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@')),
    ('prefixed_token', re.compile(r'\b(?:sk-|gh[pousr]_|github_pat_|glpat-)[A-Za-z0-9_-]{8,}\b')),
    ('bearer', re.compile(r'(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]{12,}')),
    ('jwt', re.compile(r'\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}\b')),
    # 40 位以上、字母数字混合、不含连字符的高熵串；带连字符的多为 UUID/trace id，保留以便排查。
    ('long_token', re.compile(r'\b(?=[A-Za-z0-9_]{40,}\b)(?=[A-Za-z0-9_]*[A-Za-z])(?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{40,}\b')),
    ('email', re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')),
]

REPLACERS = {
    'private_key': lambda match: PLACEHOLDER,
    'authorization': lambda match: match.group(1) + PLACEHOLDER,
    'cookie': lambda match: match.group(1) + ': ' + PLACEHOLDER,
    'credential_assignment': lambda match: match.group(1) + match.group(2) + PLACEHOLDER,
    'url_credentials': lambda match: match.group(1) + PLACEHOLDER + '@',
    'prefixed_token': lambda match: PLACEHOLDER,
    'bearer': lambda match: 'Bearer ' + PLACEHOLDER,
    'jwt': lambda match: PLACEHOLDER,
    'long_token': lambda match: PLACEHOLDER,
    'email': lambda match: PLACEHOLDER,
}


def mask_text(text):
    """返回 (脱敏文本, 命中规则名)。规则命中即整段替换，重复调用结果稳定。"""
    if not text:
        return text or '', []
    masked, notes = text, []
    for name, pattern in RULES:
        masked, count = pattern.subn(REPLACERS[name], masked)
        if count:
            notes.append(name)
    return masked, notes


def mask_data(value, *, preserve_provenance=False):
    """检索证据与模型输出也可能携带凭据，统一在写入检查点前清洗。"""
    notes = set()

    def clean(item):
        if isinstance(item, str):
            text, hits = mask_text(item)
            notes.update(hits)
            return text
        if isinstance(item, dict):
            result = {}
            for key, text in item.items():
                # 只对应用生成且已由仓库快照校验的引用字段保留哈希，避免破坏可回查性。
                verified_hash = preserve_provenance and key in ('commit', 'sha256', 'snapshot_id') and isinstance(text, str)
                result[key] = text if verified_hash and re.fullmatch(r'[a-f0-9]{40,64}', text) else clean(text)
            return result
        if isinstance(item, list):
            return [clean(text) for text in item]
        return item

    return clean(value), sorted(notes)
