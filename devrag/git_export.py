"""读取指定提交的允许文本，供手动导出与自动同步共用。"""
import hashlib
import subprocess
from datetime import datetime, timezone

from .models import SECRET, Bundle, allowed_path


def export(repo, project, ref, includes):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE, timeout=30)
    commit = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    branch = git('rev-parse', '--abbrev-ref', 'HEAD').decode().strip() if ref == 'HEAD' else ref
    files, excluded, out_of_scope = [], [], 0
    entries = git('ls-tree', '-rz', '--full-tree', commit).decode('utf-8').strip('\0').split('\0')
    for entry in entries:
        if not entry:
            continue
        metadata, path = entry.split('\t', 1)
        mode, kind, blob = metadata.split()
        if includes and not any(path == prefix or path.startswith(prefix.rstrip('/') + '/') for prefix in includes):
            out_of_scope += 1
            continue
        if mode == '120000' or kind != 'blob' or not allowed_path(path):
            excluded.append({'path': path, 'reason': '不支持的类型、敏感目录或链接'})
            continue
        raw = git('cat-file', 'blob', blob)
        if len(raw) > 150000:
            excluded.append({'path': path, 'reason': '文件过大'})
            continue
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            excluded.append({'path': path, 'reason': '非UTF-8文本'})
            continue
        if '\x00' in content or SECRET.search(content):
            excluded.append({'path': path, 'reason': '疑似密钥或二进制'})
            continue
        files.append({'path': path, 'content': content, 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()})
    return Bundle.model_validate({'schema': 1, 'project': project, 'branch': branch, 'commit': commit, 'files': files,
                                 'exported_at': datetime.now(timezone.utc).isoformat(), 'excluded': excluded,
                                 'scope': {'selected_roots': includes, 'tracked_file_count': len(entries), 'out_of_scope_count': out_of_scope}})
