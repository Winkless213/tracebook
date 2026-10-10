import json
from unittest.mock import Mock

import pytest

from scripts import maintain_knowledge_tunnel as tunnel


def test_exited_ssh_is_reconnected_and_interrupted_owned_child_is_stopped(tmp_path, monkeypatch):
    first = Mock(pid=11)
    first.wait.return_value = 255
    first.poll.return_value = 255
    second = Mock(pid=12)
    second.wait.side_effect = KeyboardInterrupt
    second.poll.return_value = None
    children, commands, pauses = iter([first, second]), [], []

    def spawn(command, **kwargs):
        commands.append(command)
        return next(children)

    monkeypatch.setattr(tunnel.subprocess, 'Popen', spawn)
    monkeypatch.setattr(tunnel.time, 'sleep', pauses.append)
    state = tmp_path / 'state/tunnel.json'
    command = ['ssh', '-N', '-L', '3415:127.0.0.1:8083', 'example-host']
    with pytest.raises(KeyboardInterrupt):
        tunnel.maintain(command, tmp_path, state)
    assert commands == [command, command]
    assert pauses == [5]
    assert json.loads(state.read_text(encoding='utf-8'))['ssh_pid'] == 12
    first.terminate.assert_not_called()
    second.terminate.assert_called_once()
