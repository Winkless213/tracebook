"""SSH 子进程退出后由驻留监督进程重连，不依赖任务计划的失败重启。"""
import json
import os
import subprocess
import time
from datetime import datetime, timezone

from filelock import FileLock

from scripts.build_knowledge import save_json


def maintain(command, work, state):
    work.mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    forward = command[command.index('-L') + 1].split(':')
    with FileLock(work / 'knowledge-tunnel.lock', timeout=0):
        with (work / 'knowledge-tunnel.out').open('a', encoding='utf-8') as out, \
                (work / 'knowledge-tunnel.err').open('a', encoding='utf-8') as err:
            while True:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                identity = {'supervisor_pid': os.getpid(), 'ssh_pid': child.pid,
                            'local_port': int(forward[0]), 'remote_port': int(forward[-1]),
                            'managed_by': 'Windows Task Scheduler + resident reconnect'}
                save_json(state, {**identity, 'state': 'running',
                                  'updated_at': datetime.now(timezone.utc).isoformat()})
                try:
                    result = child.wait()
                finally:
                    if child.poll() is None:
                        child.terminate()
                save_json(state, {**identity, 'state': 'retry_wait', 'last_exit_code': result,
                                  'updated_at': datetime.now(timezone.utc).isoformat()})
                out.write(json.dumps({'event': 'tunnel_reconnecting', 'exit_code': result}) + '\n')
                out.flush()
                time.sleep(5)
