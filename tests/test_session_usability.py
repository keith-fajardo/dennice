"""Offline regressions for inline file options, scrolling, links and folders."""
import asyncio

from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea, Tree

from dennice.runs.sessions import ChatMessage, SessionStore
from dennice.tui.app import DenniceApp
from dennice.tui.files import DirectoryPicker, FilePreviewPane, WorkspaceFiles
from dennice.tui.transcript import Transcript
from dennice.core.config import PermissionMode, ProviderConfig, ReasoningEffort


def test_file_options_open_inline_not_on_a_separate_screen(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "example.txt").write_text("example\n")
    async def run():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            await pilot.pause()
            sidebar = app.query_one(WorkspaceFiles)
            tree = sidebar.query_one(Tree)
            node = next(child for child in tree.root.children if child.data == "example.txt")
            tree.select_node(node)
            await pilot.pause()
            assert not isinstance(app.screen, ModalScreen)
            assert sidebar.query_one("#file-inline-actions").display
            assert "example.txt" in str(sidebar.query_one("#file-inline-title", Static).content)
            assert app.focused.id == "file-inline-preview"
            await pilot.press("escape")
            assert not sidebar.query_one("#file-inline-actions").display
            tree.select_node(node)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ModalScreen)
            preview = app.query_one(FilePreviewPane)
            assert preview.display
            assert preview.query_one(TextArea).text == "example\n"
            await pilot.press("escape")
            app.action_new_session()
            assert not sidebar.query_one("#file-inline-actions").display
    asyncio.run(run())


def test_explorer_single_click_expands_folders_and_context_menu_has_keyboard_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "example.txt").write_text("example\n")

    async def run():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            await pilot.pause()
            sidebar = app.query_one(WorkspaceFiles)
            tree = sidebar.query_one(Tree)
            folder = tree.root.children[0]
            assert not folder.is_expanded
            await pilot.click(tree, offset=(1, folder.line))
            await pilot.pause()
            assert folder.is_expanded

            file_node = folder.children[0]
            tree.focus()
            await pilot.click(tree, offset=(1, file_node.line), button=3)
            await pilot.pause()
            assert sidebar.query_one("#file-inline-actions").display

    asyncio.run(run())


def test_explorer_file_options_have_shift_f10_keyboard_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "example.txt").write_text("example\n")

    async def run():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            await pilot.pause()
            sidebar = app.query_one(WorkspaceFiles)
            tree = sidebar.query_one(Tree)
            node = next(child for child in tree.root.children if child.data == "example.txt")
            tree.select_node(node)
            tree.focus()
            await pilot.press("shift+f10")
            await pilot.pause()
            assert sidebar.query_one("#file-inline-actions").display
            assert "example.txt" in str(sidebar.query_one("#file-inline-title", Static).content)

    asyncio.run(run())


def test_file_explorer_cut_paste_moves_file_to_selected_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "source.txt").write_text("move me\n")
    (tmp_path / "destination").mkdir()

    async def run():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            await pilot.pause()
            sidebar = app.query_one(WorkspaceFiles)
            tree = sidebar.query_one(Tree)
            source = next(node for node in tree.root.children if node.data == "source.txt")
            destination = next(node for node in tree.root.children if getattr(node.data, "relative", None) == "destination")

            sidebar._show_file_options(source)
            sidebar.query_one("#file-inline-cut", Button).press()
            await pilot.pause()
            assert sidebar._cut_file == "source.txt"
            assert sidebar.query_one("#files-paste", Button).display
            assert not (tmp_path / "destination" / "source.txt").exists()

            tree.select_node(destination)
            paste = sidebar.query_one("#files-paste", Button)
            assert paste.display
            paste.press()
            await pilot.pause()
            app.screen.query_one("#file-operation-confirm-yes", Button).press()
            await pilot.pause()

            assert not (tmp_path / "source.txt").exists()
            assert (tmp_path / "destination" / "source.txt").read_text() == "move me\n"
            assert sidebar._cut_file is None

    asyncio.run(run())


def test_inline_preview_find_navigates_matches_and_never_edits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "example.txt"
    text = "one needle\ntwo NEEDLE\nthree needle\n"
    path.write_text(text)
    async def run():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            app.query_one(WorkspaceFiles)._open_file("example.txt", "preview", 2)
            await pilot.pause()
            pane = app.query_one(FilePreviewPane)
            content = pane.query_one(TextArea)
            assert pane.display and content.read_only
            assert content.cursor_location == (1, 0)
            assert not app.query_one("#transcript-scroll").display
            await pilot.press("ctrl+f")
            await pilot.pause()
            field = pane.query_one("#preview-find", Input)
            assert app.focused is field
            field.value = "needle"
            await pilot.pause()
            assert len(pane.matches) == 3
            assert content.selected_text == "needle"
            assert "1 / 3" in str(pane.query_one("#preview-find-status", Static).content)
            await pilot.press("enter")
            await pilot.pause()
            assert content.selected_text == "NEEDLE"
            assert "line 2" in str(pane.query_one("#preview-find-status", Static).content)
            pane.query_one("#preview-previous", Button).press()
            await pilot.pause()
            assert pane.match_index == 0
            field.value = "["
            await pilot.pause()
            assert not pane.matches
            assert "Find:" in str(pane.query_one("#preview-find-status", Static).content)
            field.value = "absent"
            await pilot.pause()
            assert "No matches" in str(pane.query_one("#preview-find-status", Static).content)
            field.focus()
            await pilot.press("escape")
            await pilot.pause()
            assert not pane.display
            assert app.query_one("#transcript-scroll").display
            assert path.read_text() == text
            app.query_one(WorkspaceFiles)._open_file("example.txt", "preview")
            await pilot.pause()
            app.action_new_session()
            await pilot.pause()
            assert not pane.display and content.text == ""
    asyncio.run(run())


def test_session_transcript_scrolls_and_keeps_reading_position(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def run():
        app = DenniceApp()
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_session()
            session = app._ensure_active_session()
            session.messages.append(ChatMessage("assistant", "\n\n".join(f"Paragraph {index}." for index in range(100))))
            app._show_transcript()
            await pilot.pause()
            scroll = app.query_one("#transcript-scroll", VerticalScroll)
            assert scroll.max_scroll_y > 100
            scroll.scroll_end(animate=False)
            await pilot.pause()
            end = scroll.scroll_y
            app.query_one(Transcript).focus(scroll_visible=False)
            await pilot.pause()
            assert isinstance(app.focused, Transcript)
            await pilot.press("pageup")
            await pilot.pause()
            assert scroll.scroll_y < end
            scroll.scroll_to(y=10, animate=False)
            await pilot.pause()
            session.messages[-1].content += "\n\nNew response text."
            app._show_transcript()
            await pilot.pause()
            assert scroll.scroll_y == 10
            app.action_new_session()
            await pilot.pause()
            assert str(app.query_one("#output", Static).content) == ""
    asyncio.run(run())


def test_markdown_links_open_on_click_and_unsafe_schemes_are_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def run():
        app = DenniceApp()
        opened = []
        app.open_url = opened.append
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_session()
            app._ensure_active_session().messages.append(ChatMessage("assistant", "[Documentation](https://example.com/docs)"))
            app._show_transcript()
            await pilot.pause()
            transcript = app.query_one(Transcript)
            rendered = transcript.render()
            link_span = next(span for span in rendered.spans if getattr(span.style, "link", None))
            assert link_span.style.meta["@click"] == ("open_link", ("https://example.com/docs",))
            # Locate the link in the actual rendered text rather than assuming
            # Markdown's padding/paragraph layout.
            offset = rendered.plain.index("Documentation")
            before = rendered.plain[:offset]
            y = before.count("\n")
            x = len(before.rsplit("\n", 1)[-1])
            await pilot.click(transcript, offset=(x + transcript.styles.padding.left + 1, y + transcript.styles.padding.top))
            await pilot.pause()
            assert opened == ["https://example.com/docs"]
            transcript.action_open_link("file:///etc/passwd")
            transcript.action_open_link("javascript:alert(1)")
            assert len(opened) == 1
    asyncio.run(run())


def test_open_folder_changes_and_persists_only_current_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "one.txt").write_text("first")
    (second / "two.txt").write_text("second")
    async def run():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.action_new_session()
            app._cwd_command(str(first))
            old = app._ensure_active_session()
            app.action_new_session()
            new = app._ensure_active_session()
            assert new.working_directory == str(tmp_path)
            sidebar = app.query_one(WorkspaceFiles)
            sidebar.query_one("#files-open-folder", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, DirectoryPicker)
            field = app.screen.query_one("#directory-path", Input)
            field.value = str(second)
            field.focus()
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#directory-confirm", Button).press()
            await pilot.pause()
            assert new.working_directory == str(second)
            assert old.working_directory == str(first)
            assert sidebar.files.root == second
            assert str(second) in str(sidebar.query_one("#file-active-directory", Static).content)
            saved = next(item for item in SessionStore(app.harness.config.runs.path, str(tmp_path)).list() if item.id == new.id)
            assert saved.working_directory == str(second)
            app._select_session(0)
            assert sidebar.files.root == first
    asyncio.run(run())


def test_bottom_status_shows_provider_model_effort_permissions_and_wraps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def run():
        app = DenniceApp()
        async with app.run_test(size=(80, 40)) as pilot:
            app.action_new_session()
            config = app.harness.config.model_copy(deep=True)
            config.executor = ProviderConfig(provider="claude", model="opus", reasoning_effort=ReasoningEffort.HIGH,
                                             permission_mode=PermissionMode.WORKSPACE_WRITE)
            app._save_config(config, "Fixture settings updated")
            await pilot.pause()
            status = app.query_one("#statusline", Static)
            assert "Model: claude/opus" in str(status.content)
            assert "Effort: high" in str(status.content)
            assert "Permissions: read-write" in str(status.content)
            assert status.size.height > 1  # long paths wrap, not crop metadata
            app._set_executor_effort("low")
            await pilot.pause()
            assert "Effort: low" in str(status.content)
    asyncio.run(run())
