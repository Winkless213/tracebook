"""只提供已发布的开发知识；中间结果与原始资料不暴露为下载。"""
import json
from pathlib import Path

from .knowledge_status import DOCUMENT_STAGES


class KnowledgeFiles:
    def __init__(self, root):
        self.root = Path(root).absolute()

    def catalog(self, root=None):
        path = (root or self.root.resolve()) / 'catalog.json'
        if not path.is_file():
            return {'projects': [], 'ready': 0}
        data = json.loads(path.read_text(encoding='utf-8'))
        projects = data['projects']
        return {'projects': projects, 'ready': sum(project['status'] == 'ready' for project in projects),
                'topics_ready': sum(project.get('topics_status') == 'ready' for project in projects),
                'history_ready': sum(project.get('history_status') == 'ready' for project in projects)}

    def document(self, project, name):
        if name not in DOCUMENT_STAGES:
            raise KeyError(name)
        root = self.root.resolve()
        entry = next((item for item in self.catalog(root)['projects'] if item['name'] == project), None)
        stage = DOCUMENT_STAGES[name]
        status = entry.get('status' if stage == 'code' else stage + '_status') if entry else None
        if not entry or status not in {'ready', 'reviewed'} or name not in entry['documents']:
            raise KeyError(project)
        path = (root / project / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise KeyError(project)
        return path
