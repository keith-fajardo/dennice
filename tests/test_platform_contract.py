import asyncio
from types import SimpleNamespace

import pytest

from dennice.core import process as processes
from dennice.core.config import DenniceConfig
from dennice.core.tools import ToolBroker


def test_git_bash_command_preserves_literal_arguments():
    import shlex
    original = ["codex", "exec", "with spaces", "$(do-not-execute)", "single'quote"]
    command = processes.command_for_platform(original, "win32")
    assert command[:2] == ["bash", "-lc"]
    assert shlex.split(command[2]) == original
    assert processes.command_for_platform(original, "darwin") == original


def test_windows_tree_cleanup_targets_only_owned_pid(monkeypatch):
    calls = []
    class FakeProcess:
        pid = 12345
        returncode = None
        async def wait(self):
            calls.append("wait")
            self.returncode = 0
    async def launch(*argv, **kwargs):
        calls.append(argv)
        return FakeProcess()
    # Replace only this module's OS view, never global os.name (Path depends on it).
    monkeypatch.setattr(processes, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(processes.asyncio, "create_subprocess_exec", launch)
    asyncio.run(processes.stop_process(FakeProcess()))
    assert calls[0] == ("taskkill", "/PID", "12345", "/T", "/F")
    assert processes.process_group_options() == {}


def test_native_file_tools_fail_closed_without_secure_descriptor_api(tmp_path, monkeypatch):
    from dennice.core import tools
    config = DenniceConfig()
    config.tools.root = str(tmp_path)
    broker = ToolBroker(config)
    monkeypatch.setattr(tools.os, "supports_dir_fd", set())
    with pytest.raises(PermissionError, match="descriptor"):
        broker._open("file.txt", tools.os.O_RDONLY)
