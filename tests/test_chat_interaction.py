"""Selection, copying and activity are tested without real provider calls."""
import asyncio

from rich.markdown import Markdown
from textual.app import App, ComposeResult
from textual.selection import Selection
from textual.geometry import Offset

from dennice.core.models import EventKind, RunEvent
from dennice.tui.app import ChatMessage, DenniceApp
from dennice.tui.transcript import Transcript


class SelectableApp(App):
    def compose(self) -> ComposeResult:
        yield Transcript(Markdown("**Hello** world"), id="transcript")


def test_rendered_markdown_is_mouse_selectable_and_copyable():
    async def journey():
        app = SelectableApp()
        async with app.run_test(size=(80, 20)) as pilot:
            widget = app.query_one(Transcript)
            assert widget.get_selection(Selection(Offset(0, 0), Offset(5, 0)))[0] == "Hello"
            await pilot.mouse_down(widget, offset=(0, 0))
            await pilot.hover(widget, offset=(5, 0))
            await pilot.mouse_up(widget, offset=(5, 0))
            await pilot.pause()
            assert "Hello" in app.screen.get_selected_text()
            widget.focus()
            await pilot.press("ctrl+c")
            assert "Hello" in app.clipboard
    asyncio.run(journey())


def test_chat_copy_response_and_file_sidebar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 40)) as pilot:
            assert not app.query_one("#workspace").display
            app._activate_workspace("")
            session = app._ensure_active_session()
            session.messages.append(ChatMessage("assistant", "**A clear answer**"))
            app._show_transcript()
            await pilot.pause()
            assert app.query_one("#workspace-files").visible
            await pilot.press("ctrl+shift+c")
            assert app.clipboard == "**A clear answer**"
            app.copy_to_clipboard("")
            app.query_one("#copy-response").press()
            await pilot.pause()
            assert app.clipboard == "**A clear answer**"
    asyncio.run(journey())


def test_activity_remains_visible_after_interim_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    class SlowExecutor:
        id, version = "offline-fixture", "test"
        async def execute(self, run_id, request):
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": "I will research this."})
            yield RunEvent(run_id=run_id, kind=EventKind.TOOL_STARTED, payload={"tool": "web_search", "operation_id": "fixture-search"})
            await release.wait()
            yield RunEvent(run_id=run_id, kind=EventKind.TOOL_COMPLETED, payload={"tool": "web_search", "operation_id": "fixture-search"})
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": " Research finished."})
    async def journey():
        nonlocal release
        release = asyncio.Event()
        app = DenniceApp()
        app.harness.executor = app.harness._executor_override = SlowExecutor()
        async with app.run_test(size=(120, 40)) as pilot:
            app._start_run("Research this")
            await pilot.pause()
            assert app._run_is_active
            status = str(app.query_one("#run-status").content)
            assert "web_search" in status
            assert "I will research" in app._ensure_active_session().messages[-1].content
            release.set()
            await app._execution_worker.wait()
            await pilot.pause()
            assert not app._run_is_active
            assert str(app.query_one("#run-status").content) == ""
    release = None
    asyncio.run(journey())


def test_compact_keeps_transcript_and_summarizes_older_turns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 40)):
            app.action_new_session()
            session = app._ensure_active_session()
            session.messages = [
                ChatMessage("user" if index % 2 == 0 else "assistant",
                            f"older-important-{index} " + "detail " * 150 if index < 6 else f"recent-{index}")
                for index in range(12)
            ]
            app._run_slash_command("/compact")
            assert session.compacted_message_count == 6
            assert len(session.messages) == 13  # Full transcript remains visible, including command receipt.
            history = app._session_conversation_history(session)
            assert len(history) == 7
            assert "older-important-0" in history[0]["content"]
            assert history[1]["content"] == "recent-6"
            assert history[-1]["content"] == "recent-11"
            session.context_used_tokens = 2_000
            session.context_window_tokens = 10_000
            session.context_provider = app.harness.config.executor.provider
            session.context_model = app.harness.config.executor.model
            assert app._context_meter() == "Ctx 8,000 left (2,000/10,000)"
    asyncio.run(journey())
