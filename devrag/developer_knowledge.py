"""只提供已发布的开发知识；中间结果与原始资料不暴露为下载。"""
import json
from pathlib import Path


class KnowledgeFiles:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def catalog(self):
        path = self.root / 'catalog.json'
        if not path.is_file():
            return {'projects': [], 'ready': 0}
        data = json.loads(path.read_text(encoding='utf-8'))
        return {'projects': data['projects'], 'ready': sum(project['status'] == 'ready' for project in data['projects'])}

    def document(self, project, name):
        if name not in {'CODEBASE.md', 'MODULES.md'}:
            raise KeyError(name)
        entry = next((item for item in self.catalog()['projects'] if item['name'] == project), None)
        if not entry or entry['status'] != 'ready' or name not in entry['documents']:
            raise KeyError(project)
        path = (self.root / project / name).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise KeyError(project)
        return path
