"""可选的 Langfuse 追踪：装了 langfuse 且配置了 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 才生效，否则全部空操作。

追踪调用出错只记日志，不影响业务结果。
"""
import logging
import os
from contextlib import ExitStack, contextmanager

logger = logging.getLogger(__name__)
LEVELS = {'failed': 'ERROR', 'rejected': 'WARNING'}


def client():
    if not (os.getenv('LANGFUSE_PUBLIC_KEY') and os.getenv('LANGFUSE_SECRET_KEY')):
        return None
    try:
        import langfuse
    except ImportError:
        logger.warning('LANGFUSE_* is set but the langfuse package is not installed; tracing is off')
        return None
    return langfuse


def _guard(call, **kwargs):
    try:
        return call(**kwargs)
    except Exception:
        logger.warning('Langfuse tracing call failed; the request itself is unaffected', exc_info=True)
        return None


class _Step:
    def __init__(self, observation):
        self.observation = observation

    def update(self, **fields):
        if self.observation is not None:
            _guard(self.observation.update, **fields)


class Run:
    """一次被追踪的处理：一个问题、一轮客服对话或一次工单分析。"""

    def __init__(self, root):
        self.root, self.open, self.statuses = root, {}, []

    def record(self, inner):
        """包装 record(model, attempt, status, elapsed, usage, error)：每次模型尝试同时记成一个 generation。"""
        def wrapped(model, attempt, status, elapsed, usage, error):
            result = inner(model, attempt, status, elapsed, usage, error)
            if self.root is None:
                return result
            if attempt is None:
                self.open[result] = _guard(self.root.start_observation, name=model, as_type='generation', model=model.partition(' / ')[0])
                return result
            self.statuses.append(status)
            generation = self.open.pop(attempt, None)
            if generation is not None:
                usage_details = {key: usage[name] for key, name in (('input', 'input_tokens'), ('output', 'output_tokens')) if usage.get(name) is not None}
                _guard(generation.update, usage_details=usage_details, level=LEVELS.get(status), status_message=error, metadata={'status': status})
                _guard(generation.end)
            return result
        return wrapped

    @contextmanager
    def span(self, name, *, as_type='span', input=None):
        observation = None if self.root is None else _guard(self.root.start_observation, name=name, as_type=as_type, input=input)
        try:
            yield _Step(observation)
        except Exception as exc:
            _Step(observation).update(level='ERROR', status_message=str(exc)[:500])
            raise
        finally:
            if observation is not None:
                _guard(observation.end)

    def finish(self, *, status, output=None, error=None):
        if self.root is None:
            return
        _guard(self.root.update, output={'error': error} if error else output, level='ERROR' if error else None, status_message=error)
        _guard(self.root.score_trace, name='outcome', value=status, data_type='CATEGORICAL')
        if self.statuses:
            passed = not error and all(item == 'completed' for item in self.statuses)
            _guard(self.root.score_trace, name='first_pass', value=1 if passed else 0, data_type='BOOLEAN')


@contextmanager
def run(name, *, as_type='chain', input=None, tags=None, session_id=None):
    langfuse = client()
    stack = ExitStack()
    root = None
    if langfuse is not None:
        try:
            stack.enter_context(langfuse.propagate_attributes(trace_name=name, tags=tags, session_id=session_id))
            root = stack.enter_context(langfuse.get_client().start_as_current_observation(name=name, as_type=as_type, input=input))
        except Exception:
            logger.warning('Langfuse tracing call failed; the request itself is unaffected', exc_info=True)
            stack.close()
            stack, root = ExitStack(), None
    with stack:
        yield Run(root)


def score_session(session_id, name, value):
    langfuse = client()
    if langfuse is not None:
        _guard(langfuse.get_client().create_score, session_id=session_id, name=name, value=1 if value else 0, data_type='BOOLEAN')
