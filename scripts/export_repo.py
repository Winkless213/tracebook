"""仅导出指定 Git 提交中的允许文本；不读取工作树未提交内容。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devrag.git_export import export

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repo', type=Path)
    parser.add_argument('--project', required=True)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--include', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = export(args.repo, args.project, args.ref, args.include)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(data.model_dump_json(by_alias=True, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(data.files), 'excluded': len(data.excluded), 'commit': data.commit, 'output': str(args.output)}, ensure_ascii=False))
