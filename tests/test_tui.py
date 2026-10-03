import asyncio
from pathlib import Path

from textual.widgets import Button

from dennice.tui.app import (
    ChatMessage,
    DenniceApp,
    ModelPickerScreen,
    SetupScreen,
    TaskComposer,
    _activity_renderable,
    _wordmark_renderable,
)


def test_tui_launches_headlessly() -> None:
    asyncio.run(_launch())


def test_wordmark_uses_a_star_for_the_i_dot() -> None:
    wordmark = _wordmark_renderable().plain
    assert wordmark.count("⭐") == 1
    assert "★" not in wordmark
    assert wordmark.count("\n") == 4


def test_activity_indicator_has_spinner_and_executor_name() -> None:
    activity = _activity_renderable("Codex", 1).plain
    assert "Working with Codex" in activity
    assert any(frame in activity for frame in "◐◓◑◒")


async def _launch() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#home-task").value == ""
        masthead = str(app.query_one("#masthead-copy").render())
        assert "Dennice" in masthead
        assert "model:" in masthead
        assert "permissions:" in masthead
        assert "⭐" in str(app.query_one("#home-title").render())
        assert app.query_one("#home-mascot")
        assert not app.query_one("#workspace").display
        assert not app.query_one("#details").display
        assert "Cognitive routing" in str(app.query_one("#routing").render())
        assert str(app.query_one("#output").render()) == ""


def test_setup_page_opens(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_open_setup())


def test_setup_saves_provider_router_and_permissions(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_save_setup())


def test_setup_connection_test_uses_unsaved_local_choices(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_test_setup_connections())


async def _test_setup_connections() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_setup()
        await pilot.pause()
        app.screen.query_one("#setup-test").press()
        await pilot.pause()
        await app.screen.workers.wait_for_complete()
        result = str(app.screen.query_one("#setup-test-result").render())
        assert "System 1 router" in result
        assert "System 2 executor" in result


def test_transcript_keeps_prior_turns(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_render_transcript())


async def _render_transcript() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app._conversation = [
            ChatMessage("user", "Investigate warehouse spend."),
            ChatMessage("assistant", "Start with daily credits."),
            ChatMessage("user", "What should I test next?"),
            ChatMessage("assistant", ""),
        ]
        app._run_is_active = True
        app._show_transcript()
        await pilot.pause()
        output = str(app.query_one("#output").render())
        assert "Investigate warehouse spend." in output
        assert "Start with daily credits." in output
        assert "Working with Mock" in output


def test_slash_help_opens_the_command_list(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_show_slash_help())


def test_slash_shows_command_autocomplete(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_show_slash_menu())


async def _show_slash_help() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app._run_slash_command("/help")
        await pilot.pause()
        output = str(app.query_one("#output").render())
        assert "Commands" in output
        assert "/setup" in output
        assert app.query_one("#workspace").display


async def _show_slash_menu() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.query_one("#home-task").value = "/"
        await pilot.pause()
        menu = app.query_one("#home-command-menu")
        assert menu.display
        assert "/benchmark" in str(menu.render())


def test_slash_menu_selects_commands_with_arrow_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_select_slash_command())


async def _select_slash_command() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.query_one("#home-task").value = "/"
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)


def test_new_session_stays_in_workspace_and_tabs_remain_selectable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_new_session_keeps_history())


async def _new_session_keeps_history() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_new_session()
        app._conversation.append(ChatMessage("user", "First task"))
        app.action_new_session()
        await pilot.pause()
        assert app.query_one("#workspace").display
        assert app.query_one("#session-tab-0").display
        assert app.query_one("#session-tab-1").display
        await pilot.click("#session-tab-0")
        await pilot.pause()
        assert "First task" in str(app.query_one("#output").render())


def test_sessions_can_be_renamed_and_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_rename_and_close_session())


async def _rename_and_close_session() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_new_session()
        app._run_slash_command("/rename Cost investigation")
        app.action_new_session()
        await pilot.pause()
        assert "Cost investigation" in str(app.query_one("#session-tab-0", Button).label)
        app.query_one("#session-close-1").press()
        await pilot.pause()
        assert app.query_one("#session-tab-0").display
        assert not app.query_one("#session-tab-1").display


def test_model_command_opens_picker_for_supported_executor(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_open_model_picker())


async def _open_model_picker() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.harness.config.executor.provider = "codex"
        app._run_slash_command("/model")
        await pilot.pause()
        assert isinstance(app.screen, ModelPickerScreen)


def test_multiline_composer_history_replays_submitted_commands(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_replay_composer_history())


async def _replay_composer_history() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        composer = app.query_one("#home-task", TaskComposer)
        app._ensure_active_session().input_history.append("/rename Saved title")
        assert app.recall_composer_history(composer, -1)
        assert composer.value == "/rename Saved title"
        composer.value = "! pwd"
        await pilot.pause()
        assert composer.has_class("terminal-mode")


async def _open_setup() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_setup()
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        assert "System 2 executor: Mock" in str(app.screen.query_one("#setup-provider").render())


async def _save_setup() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_setup()
        await pilot.pause()
        await pilot.click("#setup-claude")
        app.screen.query_one("#setup-save").press()
        await pilot.pause()
        assert app.harness.config.executor.provider == "claude"
        assert app.harness.config.executor.permission_mode is not None
        assert app.harness.config.router.provider == "rule"
        assert (Path("dennice.yaml")).exists()
