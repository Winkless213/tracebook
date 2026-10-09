import ast
from pathlib import PurePosixPath


def kind_for(path):
    if 'test' in PurePosixPath(path).parts or '/test' in path or path.startswith('test'):
        return 'test'
    if PurePosixPath(path).suffix in {'.md', '.txt', '.rst'}:
        return 'document'
    if PurePosixPath(path).suffix in {'.toml', '.json', '.yml', '.yaml'} or PurePosixPath(path).name == 'Dockerfile':
        return 'config'
    return 'code'


def split_file(path, content):
    lines = content.splitlines()
    if not lines:
        return []
    boundaries = {1: ''}
    if path.endswith('.py'):
        try:
            for node in ast.parse(content).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    boundaries[min([node.lineno] + [item.lineno for item in node.decorator_list])] = node.name
                    if isinstance(node, ast.ClassDef):
                        for method in node.body:
                            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                line = min([method.lineno] + [item.lineno for item in method.decorator_list])
                                boundaries[line] = f'{node.name}.{method.name}'
        except SyntaxError:
            pass
    elif path.endswith('.md'):
        in_fence = False
        for number, text in enumerate(lines, 1):
            if text.startswith('```'):
                in_fence = not in_fence
            if not in_fence and text.startswith('#'):
                boundaries[number] = text.lstrip('# ').strip()
    starts = sorted(boundaries) + [len(lines) + 1]
    chunks = []
    for index, start in enumerate(starts[:-1]):
        end = starts[index + 1] - 1
        cursor = start
        while cursor <= end:
            last = cursor
            while last < end and last - cursor < 69 and len('\n'.join(lines[cursor - 1:last + 1])) < 6000:
                last += 1
            text = '\n'.join(lines[cursor - 1:last])
            if len(text) > 12000:
                raise ValueError(f'单行过长无法可靠引用，请排除此文件：{path}')
            if text.strip():
                chunks.append({'path': path, 'start_line': cursor, 'end_line': last, 'content': text,
                               'kind': kind_for(path), 'symbol': boundaries[start]})
            cursor = last + 1
    return chunks
