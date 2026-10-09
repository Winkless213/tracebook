"""只读取 Git 文件清单，生成资料接入建议；不读取未提交文件或调用模型。"""
import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.indexing import kind_for
from devrag.models import allowed_path


def inspect_repository(repo, ref='HEAD'):
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--verify', ref + '^{commit}'], text=True).strip()
    tree = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-rz', '--full-tree', revision]).decode('utf-8')
    groups = {kind: [] for kind in ['document', 'code', 'test', 'config', 'excluded']}
    for entry in filter(None, tree.split('\0')):
        metadata, path = entry.split('\t', 1)
        mode, kind, _ = metadata.split()
        category = kind_for(path) if kind == 'blob' and mode != '120000' and allowed_path(path) else 'excluded'
        groups[category].append(path)
    return revision, groups


def write_report(repo, ref, output):
    revision, groups = inspect_repository(repo, ref)
    lines = ['# 项目资料接入清单', '', f'提交：`{revision}`', '',
             '本报告根据 Git 清单生成，只统计已提交文件。未读取未提交内容，未调用模型；类型可导出不代表内容已通过密钥检查。', '',
             '## 选择一个真实工作问题', '',
             '先选一个近期会查的问题，再选择相关模块、说明和测试。已有 README、交接、故障记录按需复用；不要求重写所有文档，也不采集完整聊天或监控开发过程。', '',
             '建议覆盖：项目用途与启动入口 → 一个核心流程的代码 → 对应测试 → 相关设计或故障说明。设计原因未记录时保留为未知。', '',
             '## 已提交文件', '']
    for kind, label in [('document', '说明与记录'), ('code', '代码'), ('test', '测试'), ('config', '配置')]:
        paths = groups[kind]
        lines.extend([f'### {label}：{len(paths)} 份', ''])
        if kind in {'code', 'test'} and len(paths) > 30:
            directories = Counter(str(Path(path).parent).replace('\\', '/') for path in paths)
            lines.extend(f'- `{directory}`：{count} 份' for directory, count in directories.most_common(30))
        else:
            lines.extend('- `' + path + '`' for path in paths[:60])
        lines.append('')
    lines.extend([f'自动排除的类型、链接及敏感目录：{len(groups["excluded"])} 份。', '',
                  '## 导入与更新', '',
                  '用 export_repo.py 的 --include 选择本报告中的路径，可重复传入。检查导出文件及 excluded 后导入。后续新提交使用同一 project、分支和路径重新导出，新增/修改/删除随快照更新；历史问答保留旧版本。'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {kind: len(paths) for kind, paths in groups.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(write_report(args.repo, args.ref, args.output))
