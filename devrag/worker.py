import threading
import time


class Worker:
    def __init__(self, store, retriever, model):
        self.store, self.retriever, self.model = store, retriever, model
        self.stop = threading.Event()

    def process_next(self):
        identifier = self.store.claim()
        if not identifier:
            return False
        started = time.monotonic()
        question = self.store.question(identifier)
        try:
            if question['sources']:
                sources = question['sources']
            else:
                sources = self.retriever.search(question['project_id'], question['question'], question['snapshot_id'])['sources']
                self.store.update(identifier, sources=sources)
                self.store.event(identifier, 'retrieved', f'已保存 {len(sources)} 条证据，引用固定于原快照')
            def cancelled():
                return self.stop.is_set() or self.store.question(identifier)['status'] == 'cancelled'
            if cancelled():
                return True
            if not sources:
                result = {'status': 'insufficient', 'summary': '在所选项目快照中没有找到相关依据。', 'claims': [], 'gaps': ['请核对项目、分支与问题，或补充资料后导入新快照。']}
            else:
                self.store.update(identifier, status='generating')
                self.store.event(identifier, 'generating', '正在根据证据生成并校验引用')

                def record(model, attempt, status, elapsed, usage, error):
                    if attempt is None:
                        return self.store.begin_attempt(identifier, model)
                    self.store.end_attempt(attempt, status, elapsed, usage, error)

                result = self.model.answer(question['question'], sources, record, cancelled)
            if not cancelled():
                self.store.update(identifier, status='completed', result=result, latency_ms=round((time.monotonic() - started) * 1000))
                self.store.event(identifier, 'completed', '结果已保存；引用编号与摘录已校验，事实仍需结合原文判断')
        except Exception as exc:
            if self.stop.is_set():
                return True
            error = str(exc) if isinstance(exc, RuntimeError) else '处理失败，已保存问题与证据，可稍后重试'
            self.store.update(identifier, status='failed', error=error, latency_ms=round((time.monotonic() - started) * 1000))
            self.store.event(identifier, 'failed', error)
        return True

    def run(self, stop):
        self.stop = stop
        self.store.recover()
        while not stop.is_set():
            if not self.process_next():
                stop.wait(0.5)
