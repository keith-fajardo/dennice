import asyncio
import os
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static, TextArea, Tree

from dennice.tui.files import (
    DiscardEditsScreen,
    FileActionScreen,
    FileEditorScreen,
    FileSafetyError,
    ProjectFiles,
    WorkspaceFiles,
    WorkspaceReplaceScreen,
    replacement_preview,
)


def test_scope_hidden_symlinks_binary_and_limits(tmp_path):
    files = ProjectFiles(tmp_path)
    for name in ("../escape", "/absolute", ".env", ".env.local", "secrets.env", ".git/config", ".ssh/key"):
        with pytest.raises(FileSafetyError):
            files.read(name)
    (tmp_path / "binary").write_bytes(b"\x00data")
    (tmp_path / "large").write_bytes(b"a" * 512001)
    for name in ("binary", "large"):
        with pytest.raises(FileSafetyError):
            files.read(name)
    (tmp_path / "visible.txt").write_text("hello")
    (tmp_path / "link").symlink_to(tmp_path / "visible.txt")
    with pytest.raises((FileSafetyError, OSError)):
        files.read("link")
    (tmp_path / ".env").write_text("not read")
    names = [name for name, _directory in files.entries()[0]]
    assert "link" not in names and ".env" not in names
    assert "visible.txt" in names


def test_atomic_save_mode_and_conflict(tmp_path):
    path = tmp_path / "example.txt"
    path.write_text("first\n")
    path.chmod(0o640)
    files = ProjectFiles(tmp_path)
    original = files.read("example.txt")
    updated = files.save(original, "second\n")
    assert updated.text == "second\n"
    assert path.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".dennice-edit-*"))
    path.write_text("outside change\n")
    with pytest.raises(FileSafetyError, match="changed outside"):
        files.save(updated, "must not clobber")
    assert path.read_text() == "outside change\n"


def test_hardlinks_refused(tmp_path):
    path = tmp_path / "one"
    path.write_text("hello")
    os.link(path, tmp_path / "two")
    with pytest.raises(FileSafetyError):
        ProjectFiles(tmp_path).read("one")


def test_regex_replacement_is_bounded_and_supports_backreferences():
    assert replacement_preview("old one\nold two", r"old (\w+)", r"new \1") == ("new one\nnew two", 2)
    with pytest.raises(FileSafetyError, match="4,096"):
        replacement_preview("x" * 5000, "x", "y")
    with pytest.raises(FileSafetyError, match="512 KB"):
        replacement_preview("x" * 1000, "x", "y" * 1000)


def test_compact_controls_and_workspace_replace_preview_save_undo(tmp_path):
    path = tmp_path / "example.txt"
    path.write_text("old value\n")
    async def journey():
        app = FilesApp(tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            assert app.query_one("#file-search").size.height == 1
            app.push_screen(FileActionScreen("example.txt"))
            await pilot.pause()
            for name in ("#file-show", "#file-edit", "#file-action-cancel"):
                assert app.screen.query_one(name).size.height == 1
            await pilot.press("escape")
            app.query_one("#files-replace").press()
            await pilot.pause()
            assert isinstance(app.screen, WorkspaceReplaceScreen)
            app.screen.query_one("#workspace-find", Input).value = "old"
            app.screen.query_one("#workspace-replacement", Input).value = "new"
            await pilot.pause()
            assert len(app.screen.results) == 1
            assert path.read_text() == "old value\n"
            app.screen.query_one("#replace-preview").press()
            await pilot.pause()
            assert isinstance(app.screen, FileEditorScreen)
            editor = app.screen.query_one(TextArea)
            assert editor.text == "new value\n"
            assert path.read_text() == "old value\n"
            await pilot.press("ctrl+z")
            assert editor.text == "old value\n"
            await pilot.press("ctrl+shift+z")
            assert editor.text == "new value\n"
            await pilot.press("ctrl+shift+s")
            assert path.read_text() == "new value\n"
            app.screen.query_one("#editor-find", Input).value = "["
            await pilot.pause()
            assert "Find/replace:" in str(app.screen.query_one("#file-replace-status", Static).content)
    asyncio.run(journey())


class FilesApp(App):
    def __init__(self, root: Path):
        super().__init__()
        self.root_path = root
    def compose(self) -> ComposeResult:
        yield WorkspaceFiles(self.root_path)


def test_browser_regex_hierarchy_and_file_actions(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "hello.py").write_text("print('hello')")
    (tmp_path / "other.txt").write_text("other")
    asyncio.run(_browser(tmp_path))


async def _browser(root):
    app = FilesApp(root)
    async with app.run_test(size=(100, 35)) as pilot:
        sidebar = app.query_one(WorkspaceFiles)
        sidebar.query_one("#file-search", Input).value = r"hello\.py$"
        await pilot.pause()
        tree = sidebar.query_one(Tree)
        assert len(tree.root.children) == 1
        directory = tree.root.children[0]
        assert str(directory.label) == "nested"
        assert directory.children[0].data == "nested/hello.py"
        sidebar.refresh_files("[")
        assert "Search:" in str(sidebar.query_one("#file-search-status", Static).render())
        sidebar._open_file("nested/hello.py", "edit")
        await pilot.pause()
        assert isinstance(app.screen, FileEditorScreen)
        editor = app.screen.query_one(TextArea)
        editor.insert("# comment\n")
        await pilot.press("ctrl+shift+s")
        await pilot.pause()
        assert "# comment" in (root / "nested/hello.py").read_text()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, FileEditorScreen)
        app.push_screen(FileActionScreen("nested/hello.py"))
        await pilot.pause()
        assert app.focused.id == "file-show"
        await pilot.press("escape")


def test_dirty_close_guard_and_undo_redo(tmp_path):
    (tmp_path / "hello.txt").write_text("hello")
    asyncio.run(_dirty(tmp_path))


async def _dirty(root):
    app = FilesApp(root)
    async with app.run_test(size=(100, 35)) as pilot:
        app.query_one(WorkspaceFiles)._open_file("hello.txt", "edit")
        await pilot.pause()
        editor = app.screen.query_one(TextArea)
        editor.insert("changed")
        await pilot.press("ctrl+z")
        assert editor.text == "hello"
        await pilot.press("ctrl+shift+z")
        assert "changed" in editor.text
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DiscardEditsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, FileEditorScreen)
        assert (root / "hello.txt").read_text() == "hello"
