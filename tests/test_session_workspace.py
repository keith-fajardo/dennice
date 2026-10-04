import asyncio
import json

from textual.widgets import Input, OptionList, RichLog, Static, TextArea, Tree

from dennice.core.models import EventKind, RunEvent
from dennice.runs.sessions import ChatSession, SessionStore
from dennice.tui.app import DenniceApp
from dennice.tui.files import FileMatch, GitDiffPane, WorkspaceFiles


def test_legacy_sessions_default_to_launch_directory(tmp_path):
    store = SessionStore(tmp_path / "runs", str(tmp_path))
    session = ChatSession.new("Legacy", str(tmp_path))
    store.save(session)
    with store._store._connection() as db:
        row = db.execute("SELECT state FROM sessions").fetchone()
        data = json.loads(row[0])
        for key in ("working_directory", "file_filter", "file_content_filter", "file_replacement"):
            data.pop(key)
        db.execute("UPDATE sessions SET state=?", (json.dumps(data),))
    loaded = store.list()[0]
    assert loaded.working_directory == str(tmp_path)
    assert loaded.file_filter == ""


def test_ui_extension_trust_does_not_transfer_to_another_directory(tmp_path):
    import pytest
    from dennice.core.config import HookConfig, MCPServerConfig
    from dennice.core.hooks import HookManager
    from dennice.core.mcp import MCPManager
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    hook = HookConfig(name="fixture", event="before_route", command=["never-launch"], enabled=True)
    hooks = HookManager()
    hooks.trust(hook, root=first)
    assert hooks.is_trusted(hook, first) and not hooks.is_trusted(hook, second)
    with pytest.raises(PermissionError):
        asyncio.run(hooks.dispatch([hook], "before_route", {}, str(second)))
    server = MCPServerConfig(name="fixture", command=["never-launch"], enabled=True)
    mcp = MCPManager()
    mcp.trust(server, root=first)
    assert mcp.is_trusted(server, first) and not mcp.is_trusted(server, second)
    async def check():
        with pytest.raises(PermissionError):
            async with mcp.connect([server], root=second):
                raise AssertionError("An untrusted directory must not connect")
    asyncio.run(check())


def test_directory_filters_tree_preview_and_reopen_are_session_scoped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "first.txt").write_text("line one\nneedle-one value\n")
    (two / "second.txt").write_text("needle-two value\n")
    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            app._cwd_command(str(one))
            first = app._ensure_active_session()
            sidebar = app.query_one(WorkspaceFiles)
            field = sidebar.query_one("#file-search", Input)
            field.value = "needle-one"
            await pilot.pause()
            assert first.file_filter == "needle-one"
            file_node = sidebar.query_one(Tree).root.children[0]
            assert file_node.data == "first.txt"
            assert isinstance(file_node.children[0].data, FileMatch)
            assert file_node.children[0].data.line == 2
            assert "needle-one value" in str(file_node.children[0].label)
            field.focus()
            await pilot.press("ctrl+a")
            assert field.selected_text == "needle-one"
            find, replace, refresh = (sidebar.query_one(name) for name in ("#files-find", "#files-replace", "#files-refresh"))
            assert find.region.y == replace.region.y
            assert refresh.region.y > find.region.y
            assert find.region.x < replace.region.x
            assert find.region.y > field.region.bottom
            app.action_new_session()
            assert app._ensure_active_session().working_directory == str(tmp_path)
            app._cwd_command(str(two))
            second = app._ensure_active_session()
            field.value = "needle-two"
            await pilot.pause()
            assert second.file_filter == "needle-two"
            app._select_session(0)
            await pilot.pause()
            assert field.value == "needle-one"
            assert sidebar.files.root == one
            assert str(one) in str(sidebar.query_one("#file-active-directory", Static).content)
            assert sidebar.files.read("first.txt").text.startswith("line one")
            app._close_session(0)
            app._resume_session(first.id)
            await pilot.pause()
            assert app._ensure_active_session().working_directory == str(one)
            assert field.value == "needle-one"
        restarted = DenniceApp()
        async with restarted.run_test(size=(120, 45)) as pilot:
            assert restarted.query_one("#home-session-list", OptionList).option_count == 2
            index = next(i for i, s in enumerate(restarted._sessions) if s.id == first.id)
            listing = restarted.query_one("#home-session-list", OptionList)
            listing.highlighted = restarted._home_session_ids.index(first.id)
            listing.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert restarted._active_session_index == index
            assert restarted.query_one(WorkspaceFiles).files.root == one
            assert restarted.query_one("#file-search", Input).value == "needle-one"
    asyncio.run(journey())


def test_terminal_and_agent_use_captured_session_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "session-root"
    directory.mkdir()
    class Executor:
        id, version = "fixture", "test"
        root = None
        def configure_runtime(self, **kwargs):
            self.root = kwargs["root"]
        async def execute(self, run_id, request):
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": "Done"})
    async def journey():
        app = DenniceApp()
        executor = Executor()
        app.harness._executor_override = executor
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            app._cwd_command(str(directory))
            app._start_terminal_command("pwd")
            await app._execution_worker.wait()
            assert str(directory) in app._ensure_active_session().messages[-1].content
            app._start_run("hello")
            await app._execution_worker.wait()
            assert executor.root == str(directory)
            assert app.harness.config.tools.root == "."
    asyncio.run(journey())


def test_branch_indicator_updates_without_changing_repository(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.chdir(tmp_path)
    repository = tmp_path / "project"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "fixture-branch", str(repository)], check=True, capture_output=True)
    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            app._cwd_command(str(repository))
            worker = app._refresh_git_branch(app._ensure_active_session().id, str(repository))
            await worker.wait()
            assert str(app.query_one("#file-git-branch", Static).content) == "Git: fixture-branch"
            assert "fixture-branch" in str(app.query_one("#statusline", Static).content)
    asyncio.run(journey())


def test_git_arrow_selection_updates_side_by_side_added_removed_lines(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.chdir(tmp_path)
    repository = tmp_path / "project"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    (repository / "a.txt").write_text("before a\n")
    (repository / "b.txt").write_text("before b\n")
    subprocess.run(["git", "-C", str(repository), "add", "a.txt", "b.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture"], check=True, capture_output=True)
    (repository / "a.txt").write_text("after a\n")
    (repository / "b.txt").write_text("after b\n")

    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            app._cwd_command(str(repository))
            sidebar = app.query_one(WorkspaceFiles)
            worker = sidebar.refresh_git_status()
            await worker.wait()
            sidebar._select_view("git")
            await pilot.pause()
            pane = app.query_one(GitDiffPane)
            assert pane.display
            assert "a.txt" in str(pane.query_one("#git-diff-title", Static).content)
            base_log = pane.query_one("#git-diff-base", RichLog)
            worktree_log = pane.query_one("#git-diff-current", RichLog)
            assert not base_log.auto_scroll and not worktree_log.auto_scroll
            assert base_log.scroll_y == 0 and worktree_log.scroll_y == 0
            base_lines = base_log.lines
            worktree_lines = worktree_log.lines
            assert any("− before a" in line.text for line in base_lines)
            assert any("+ after a" in line.text for line in worktree_lines)
            await pilot.press("down")
            await pilot.pause()
            assert "b.txt" in str(pane.query_one("#git-diff-title", Static).content)
            base_lines = pane.query_one("#git-diff-base", RichLog).lines
            worktree_lines = pane.query_one("#git-diff-current", RichLog).lines
            assert any("− before b" in line.text for line in base_lines)
            assert any("+ after b" in line.text for line in worktree_lines)
    asyncio.run(journey())
