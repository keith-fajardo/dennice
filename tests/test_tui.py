import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from textual.widgets import Button, Select, Static

from dennice.core.models import EventKind, RunEvent, Task
from dennice.tui.app import (
    ApprovalScreen,
    ChatMessage,
    DenniceApp,
    ModelPickerScreen,
    SetupScreen,
    SkillPickerScreen,
    TaskComposer,
    EXECUTOR_MODELS,
    _activity_renderable,
    _wordmark_renderable,
)


def test_new_harness_controls_preserve_runtime_trust(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asyncio.run(_harness_controls())


async def _harness_controls():
    app = DenniceApp()
    async with app.run_test() as pilot:
        hooks, mcp = app.harness.hooks, app.harness.mcp
        app._routing_command("shadow")
        app._routing_command("unpin-model")
        app._pool_command("add test-model balanced")
        app._tools_command("on")
        assert app.harness.hooks is hooks and app.harness.mcp is mcp
        assert app.harness.approve is not None
        assert app.harness.config.routing.mode == "shadow"
        assert not app.harness.config.routing.model_pinned
        assert app.harness.config.routing.model_pool["mock"][0].model == "test-model"
        assert app.harness.config.tools.enabled
        app.push_screen(ApprovalScreen("Approve?", "Exact command"))
        await pilot.pause()
        assert app.focused.id == "approval-deny"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ApprovalScreen)


def test_hook_trust_requires_confirmation_and_does_not_launch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asyncio.run(_confirm_hook_trust())


async def _confirm_hook_trust():
    from dennice.core.config import HookConfig
    from dennice.core.hooks import fingerprint
    app = DenniceApp()
    app.harness.config.hooks = [HookConfig(name="demo", event="before_route", command=["never-launch"], enabled=True)]
    async with app.run_test() as pilot:
        await pilot.pause()
        worker = app._extension_command("hooks", "trust demo")
        await pilot.pause()
        assert isinstance(app.screen, ApprovalScreen)
        assert "never-launch" in app.screen.detail
        await pilot.press("escape")
        await worker.wait()
        assert not app.harness.hooks.trusted
        worker = app._extension_command("hooks", "trust demo")
        await pilot.pause()
        app.screen.query_one("#approval-allow", Button).press()
        await worker.wait()
        assert fingerprint(app.harness.config.hooks[0]) in app.harness.hooks.trusted
        await pilot.pause()


def test_mcp_test_reports_tools_resources_and_prompts_accurately(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asyncio.run(_mcp_test_reports_capability_counts(tmp_path, monkeypatch))


async def _mcp_test_reports_capability_counts(root, monkeypatch):
    from dennice.core.config import MCPServerConfig

    app = DenniceApp()
    server = MCPServerConfig(name="demo", enabled=True)
    app.harness.config.mcp = [server]
    app.harness.mcp.trust(server, root=str(root))
    app._session_directory = lambda _session: root
    messages = []
    app._show_local_message = messages.append

    @asynccontextmanager
    async def connected(*_args, **_kwargs):
        yield SimpleNamespace(
            status=[{"approved_tools": 1, "approved_resources": 2, "approved_prompts": 3}],
            definitions=lambda: ["a"] * 6,
        )

    monkeypatch.setattr(app.harness.mcp, "connect", connected)
    async with app.run_test():
        worker = app._extension_command("mcp", "test demo")
        await worker.wait()
    assert "1 approved tools" in messages[-1]
    assert "2 approved resources" in messages[-1]
    assert "3 approved prompts discovered" in messages[-1]
    assert "No tools invoked and no resource or prompt content fetched." in messages[-1]


def test_goal_without_independent_checks_stops_for_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asyncio.run(_goal_requires_checks())


def test_api_skill_requires_tools_and_explicit_selection(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.chdir(tmp_path)
    skill = SimpleNamespace(key="claude/demo", name="demo", provider="claude", path=tmp_path / "SKILL.md")
    monkeypatch.setattr("dennice.tui.app.discover_skills", lambda project=None: [skill])
    app = DenniceApp()
    app.harness.config.executor.provider = "openai-api"
    messages, requests = [], []
    monkeypatch.setattr(app, "_show_local_message", messages.append)
    monkeypatch.setattr(app, "_start_run", lambda prompt, selected_skill: requests.append((prompt, selected_skill)))
    app._run_skill("demo Explain this")
    assert not requests and "/tools on" in messages[-1]
    app.harness.config.tools.enabled = True
    from dennice.runs.sessions import ChatSession
    app._sessions = [ChatSession.new("Test", str(tmp_path))]
    app._active_session_index = 0
    app._run_skill("demo Explain this")
    assert requests[0][0] == "Explain this"
    assert requests[0][1]["path"] == str(skill.path)


async def _goal_requires_checks():
    app = DenniceApp()
    async with app.run_test() as pilot:
        app._goal_command("Build a report")
        await app._execution_worker.wait()
        session = app._ensure_active_session()
        assert session.goal_id
        assert app._goals.get(session.goal_id).status == "awaiting_input"
        assert "Configure independent completion checks" in session.messages[-1].content


def rendered_output(app):
    console = Console(record=True, width=120)
    console.print(app.query_one("#output").content)
    return console.export_text()


@pytest.fixture(autouse=True)
def stub_claude_catalog(monkeypatch):
    async def catalog():
        return EXECUTOR_MODELS["claude"]

    monkeypatch.setattr("dennice.tui.app.load_claude_model_catalog", catalog)

    async def api_catalog(config):
        return (("Test API model", "test-api-model"), ("Custom model…", "custom"))

    monkeypatch.setattr("dennice.tui.app.load_api_models", api_catalog)

    async def codex_catalog():
        return EXECUTOR_MODELS["codex"]

    monkeypatch.setattr("dennice.tui.app.load_codex_model_catalog", codex_catalog)


def test_escape_closes_setup_without_saving(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asyncio.run(_escape_setup())


async def _escape_setup():
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_setup()
        await pilot.pause()
        app.screen.executor_provider = "claude"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SetupScreen)
        assert app.harness.config.executor.provider == "mock"
        assert not Path("dennice.yaml").exists()


@pytest.mark.parametrize("provider", ["openai-api", "anthropic-api", "local"])
def test_setup_and_model_picker_support_api_providers(tmp_path, monkeypatch, provider):
    monkeypatch.chdir(tmp_path)
    asyncio.run(_api_setup(provider))


async def _api_setup(provider):
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_setup()
        await pilot.pause()
        app.screen.query_one(f"#setup-{provider}", Button).press()
        await pilot.pause()
        assert app.screen.executor_provider == provider
        assert app.screen.query_one("#setup-api-url").display
        app.screen.query_one("#setup-model-choice", Select).value = "test-api-model"
        app.screen.query_one("#setup-save", Button).press()
        await pilot.pause()
        assert app.harness.config.executor.provider == provider
        assert app.harness.config.executor.model == "test-api-model"
        app._run_slash_command("/model")
        await pilot.pause()
        assert isinstance(app.screen, ModelPickerScreen)
        assert ("Test API model", "test-api-model") in app.screen._options


def test_setup_keeps_claude_cli_auth_choice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            app.action_setup()
            await pilot.pause()
            app.screen.query_one("#setup-claude", Button).press()
            await pilot.pause()
            auth = app.screen.query_one("#setup-claude-auth", Select)
            assert auth.display and auth.value == "subscription"
            auth.value = "api_key"
            app.screen.query_one("#setup-save", Button).press()
            await pilot.pause()
            assert app.harness.config.executor.claude_cli_auth == "api_key"
            app.action_setup()
            await pilot.pause()
            assert app.screen.query_one("#setup-claude-auth", Select).value == "api_key"

    asyncio.run(check())


def test_setup_keeps_codex_cli_auth_choice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            app.action_setup()
            await pilot.pause()
            app.screen.query_one("#setup-codex", Button).press()
            await pilot.pause()
            auth = app.screen.query_one("#setup-codex-auth", Select)
            assert auth.display and auth.value == "chatgpt"
            auth.value = "api_key"
            app.screen.query_one("#setup-save", Button).press()
            await pilot.pause()
            assert app.harness.config.executor.codex_cli_auth == "api_key"
            app.action_setup()
            await pilot.pause()
            assert app.screen.query_one("#setup-codex-auth", Select).value == "api_key"

    asyncio.run(check())


def test_setup_keeps_codex_router_auth_choice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            app.action_setup()
            await pilot.pause()
            app.screen.query_one("#router-codex", Button).press()
            await pilot.pause()
            app.screen.query_one("#setup-codex", Button).press()
            await pilot.pause()
            auth = app.screen.query_one("#setup-router-codex-auth", Select)
            assert auth.display and auth.value == "chatgpt"
            auth.value = "api_key"
            app.screen.query_one("#setup-save", Button).press()
            await pilot.pause()
            assert app.harness.config.router.codex_cli_auth == "api_key"
            app.action_setup()
            await pilot.pause()
            assert app.screen.query_one("#setup-router-codex-auth", Select).value == "api_key"

    asyncio.run(check())


def test_skills_picker_prepares_prompt_without_running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from dennice.core.skills import LocalSkill
    skill = LocalSkill("codex:project0:review", "review", "Review code", "codex", tmp_path / "SKILL.md")
    monkeypatch.setattr("dennice.tui.app.discover_skills", lambda project=None: [skill])
    asyncio.run(_select_skill())


async def _select_skill():
    app = DenniceApp()
    async with app.run_test() as pilot:
        app._run_slash_command("/skills")
        await pilot.pause()
        assert isinstance(app.screen, SkillPickerScreen)
        app.screen.query_one("#skill-use", Button).press()
        await pilot.pause()
        assert app.query_one("#home-task", TaskComposer).value == "/skill codex:project0:review "
        assert not app._run_is_active


def test_assistant_markdown_is_rendered(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asyncio.run(_markdown_response())


async def _markdown_response():
    app = DenniceApp()
    async with app.run_test() as pilot:
        app._conversation = [ChatMessage("assistant", "**Hello**\n\n| Name | Value |\n|---|---|\n| A | B |")]
        app._show_transcript()
        await pilot.pause()
        output = rendered_output(app)
        assert "Hello" in output
        assert "**Hello**" not in output
        assert "|---|---|" not in output
        assert "Name" in output and "Value" in output


def test_ctrl_v_attaches_image_without_submitting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from PIL import Image
    path = tmp_path / "paste.png"
    Image.new("RGB", (8, 8)).save(path)
    monkeypatch.setattr("dennice.tui.app.paste_image", lambda directory: path)
    asyncio.run(_paste_image())


async def _paste_image():
    app = DenniceApp()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert len(app._ensure_active_session().pending_images) == 1
        assert app.query_one("#home-attachments").display
        assert not app._run_is_active
        app._run_slash_command("/clear-images")
        await pilot.pause()
        assert not app._ensure_active_session().pending_images


def test_tui_launches_headlessly(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_launch())


def test_session_history_survives_restart_and_archive_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def check():
        first = DenniceApp()
        async with first.run_test() as pilot:
            first.action_new_session()
            session = first._ensure_active_session()
            session.title = "Saved session"
            session.messages.append(ChatMessage("user", "Remember this"))
            saved_id = session.id
            first._render_session_tabs()
            first._close_session(0)
            await pilot.pause()
        second = DenniceApp()
        async with second.run_test() as pilot:
            assert second.query_one("#home").display
            assert not second.query_one("#workspace").display
            second._run_slash_command(f"/resume {saved_id}")
            await pilot.pause()
            assert second._ensure_active_session().id == saved_id
            assert "Remember this" in rendered_output(second)
        third = DenniceApp()
        assert any(session.id == saved_id for session in third._sessions)
    asyncio.run(check())


def test_terminal_commands_do_not_enter_next_model_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def check():
        app = DenniceApp()
        captured = []
        monkeypatch.setattr(app, "_run_terminal", lambda *args: None)
        monkeypatch.setattr(app, "_run", lambda task, *args: captured.append(task))
        async with app.run_test():
            app._start_terminal_command("echo private-command")
            app._start_run("hello")
            assert "private-command" not in str(captured[0].context)
            assert captured[0].metadata["session_id"] == app._ensure_active_session().id
    asyncio.run(check())


def test_setup_arrow_keys_navigate_buttons_and_preserve_dropdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def catalog():
        return (("Use Claude Code default", "default"), ("Sonnet 5.5", "sonnet"),
                ("Opus 5.5", "opus"), ("Custom model…", "custom"))
    monkeypatch.setattr("dennice.tui.app.load_claude_model_catalog", catalog)
    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            app.action_setup()
            await pilot.pause()
            screen = app.screen
            screen.query_one("#setup-codex", Button).focus()
            await pilot.press("right")
            assert screen.focused.id == "setup-claude"
            await pilot.press("enter")
            await pilot.pause()
            assert screen.executor_provider == "claude"
            selector = screen.query_one("#setup-model-choice", Select)
            assert selector.value == "default"
            assert "Claude" in str(selector.query_one("SelectCurrent #label", Static).render())
            screen.query_one("#setup-claude", Button).focus()
            await pilot.press("down")
            assert screen.focused.id == "setup-openai-api"
            await pilot.press("up")
            assert screen.focused.id == "setup-claude"
            selector.focus()
            await pilot.press("enter", "down", "enter")
            await pilot.pause()
            assert selector.value == "sonnet"
    asyncio.run(check())


def test_setup_can_save_github_copilot_subscription_executor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            app.action_setup()
            await pilot.pause()
            await pilot.click("#setup-copilot")
            await pilot.pause()
            screen = app.screen
            assert screen.executor_provider == "copilot"
            note = screen.query_one("#setup-copilot-note", Static)
            assert note.display
            assert "GITHUB_TOKEN" in str(note.render())
            assert "opted out" in str(note.render())
            assert ("GPT-5.4", "gpt-5.4") in screen._model_options()
            screen.query_one("#setup-save", Button).press()
            await pilot.pause()
            assert app.harness.config.executor.provider == "copilot"

    asyncio.run(check())


def test_sessions_command_opens_keyboard_picker_and_rename_updates_tab(tmp_path, monkeypatch):
    from dennice.tui.app import SessionPickerScreen
    monkeypatch.chdir(tmp_path)
    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            composer = app.query_one("#home-task", TaskComposer)
            composer.value = "/rename Test1"
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert app.query_one("#workspace").display
            assert "Test1" in str(app.query_one("#session-tab-0", Button).label)
            app.action_new_session()
            app._rename_current_session("Test2")
            composer = app.query_one("#workspace-task", TaskComposer)
            composer.value = "/sessions"
            composer.focus()
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert isinstance(app.screen, SessionPickerScreen)
            assert [session.title for session in app.screen.sessions] == ["Test1", "Test2"]
            await pilot.press("down", "enter")
            await pilot.pause()
            assert app._ensure_active_session().title == "Test2"
            app._close_session(0)
            app._run_slash_command("/sessions")
            await pilot.pause()
            assert isinstance(app.screen, SessionPickerScreen)
            assert any(session.title == "Test1" and session.closed for session in app.screen.sessions)
            await pilot.press("enter")
            await pilot.pause()
            assert app._ensure_active_session().title == "Test1"
    asyncio.run(check())


def test_wordmark_uses_a_star_for_the_i_dot() -> None:
    wordmark = _wordmark_renderable().plain
    assert wordmark.count("⭐") == 1
    assert "★" not in wordmark
    assert wordmark.count("\n") == 4


def test_codex_model_fallback_includes_terra() -> None:
    assert ("GPT-5.6 Terra", "gpt-5.6-terra") in EXECUTOR_MODELS["codex"]


def test_activity_indicator_has_spinner_and_executor_name() -> None:
    activity = _activity_renderable("Codex", 1).plain
    assert "Working with Codex" in activity
    assert any(frame in activity for frame in "◐◓◑◒")


def test_activity_status_aligns_with_chat_pane(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def check():
        app = DenniceApp()
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_session()
            await pilot.pause()
            agent = app.query_one("#agent")
            status = app.query_one("#run-status")
            output = app.query_one("#output")
            session = app._ensure_active_session()
            app._execution_worker = SimpleNamespace(is_finished=False)
            app._running_session_id = session.id
            app._run_is_active = True
            app._show_activity()
            await pilot.pause()
            assert status.parent is agent
            assert status.region.x == output.region.x + 2
            assert "Working with" in str(status.render())

    asyncio.run(check())


def test_failed_run_keeps_partial_response_visible(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class FailedHarness:
        _last_trace = None
        config = SimpleNamespace(budgets=SimpleNamespace(max_total_tokens=100000))

        async def run_events(self, task):
            yield RunEvent(run_id="run-test", kind=EventKind.MODEL_STREAM, payload={"text": "Partial analysis"})
            yield RunEvent(run_id="run-test", kind=EventKind.USAGE, payload={
                "phase": "executor", "provider": "codex", "model": "gpt-5.6-terra",
                "reported": True, "attempt_id": "codex:run-test", "cumulative": True,
                "input_tokens": 50000, "output_tokens": 500,
            })
            yield RunEvent(run_id="run-test", kind=EventKind.USAGE, payload={
                "phase": "executor", "provider": "codex", "model": "gpt-5.6-terra",
                "reported": True, "attempt_id": "codex:run-test", "cumulative": True,
                "input_tokens": 108379, "output_tokens": 1967,
                "context_tokens": 32473, "context_window_tokens": 258400,
            })
            yield RunEvent(run_id="run-test", kind=EventKind.RUN_FAILED, payload={"error": "Token budget reached"})

    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            app.action_new_session()
            session = app._ensure_active_session()
            message = ChatMessage("assistant", "")
            session.messages.append(message)
            monkeypatch.setattr(app, "_session_harness", lambda session: FailedHarness())
            await app._run(Task(prompt="Analyze"), message, session).wait()
            await pilot.pause()
            assert "Partial response (unverified)" in message.content
            assert "Partial analysis" in message.content
            assert "Token budget reached" in message.content
            assert session.last_run_tokens == 110346
            assert "Last run 110,346/100,000 tokens" in app._status_text()
            assert "Last run 110,346/100,000 tokens" in str(app.query_one("#statusline", Static).render())
            assert "Ctx " in app._status_text()

    asyncio.run(check())


def test_auto_route_displays_effective_model_and_reported_context(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def check():
        app = DenniceApp()
        async with app.run_test() as pilot:
            app.action_new_session()
            session = app._ensure_active_session()
            configured_model = app.harness.config.executor.model
            provider = app.harness.config.executor.provider

            class RoutedHarness:
                _last_trace = None
                config = SimpleNamespace(
                    budgets=SimpleNamespace(max_total_tokens=100000),
                    executor=SimpleNamespace(provider=provider, model=configured_model),
                )

                async def run_events(self, task):
                    yield RunEvent(run_id="routed", kind=EventKind.ROUTE_SELECTED, payload={
                        "effective_model": "routed-small", "effective_effort": "high"})
                    yield RunEvent(run_id="routed", kind=EventKind.USAGE, payload={
                        "phase": "executor", "provider": provider, "model": "routed-small",
                        "reported": True, "input_tokens": 120, "output_tokens": 30,
                        "context_tokens": 48, "context_window_tokens": 64,
                    })
                    yield RunEvent(run_id="routed", kind=EventKind.RUN_COMPLETED, payload={"verified": False})

            message = ChatMessage("assistant", "")
            session.messages.append(message)
            monkeypatch.setattr(app, "_session_harness", lambda session: RoutedHarness())
            await app._run(Task(prompt="Analyze"), message, session).wait()
            await pilot.pause()
            status = app._status_text()
            assert "Last route: routed-small/high" in status
            assert "Ctx 16 left (48/64)" in status
            restored = next(item for item in app._session_store.list() if item.id == session.id)
            assert restored.last_route_effective_model == "routed-small"
            app.harness.config.executor.model = "changed-model"
            assert "Last route:" not in app._status_text()
            assert "Ctx ~" in app._status_text()

    asyncio.run(check())


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
        assert not app.screen.query_one("#setup-test-executor", Button).display
        assert app.screen.query_one("#setup-executor-test-result").render().plain == ""
        assert str(app.screen.query_one("#setup-router-test-result").render()) == ""
        app.screen.query_one("#setup-test-router").press()
        await pilot.pause()
        await app.screen.workers.wait_for_complete()
        assert "Rule router" in str(app.screen.query_one("#setup-router-test-result").render())


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
        output = rendered_output(app)
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
        output = rendered_output(app)
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
        assert "First task" in rendered_output(app)


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
    async def catalog():
        return (("Default", "default"), ("Test Terra", "test-terra"),
                ("Test Extra", "test-extra"), ("Custom model…", "custom"))

    monkeypatch.setattr("dennice.tui.app.load_codex_model_catalog", catalog)
    asyncio.run(_open_model_picker())


async def _open_model_picker() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.harness.config.executor.provider = "codex"
        composer = app.query_one("#home-task", TaskComposer)
        composer.value = "/model"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ModelPickerScreen)
        assert app.harness.config.executor.model != "<name>"
        assert ("Test Terra", "test-terra") in app.screen._options
        assert ("Test Extra", "test-extra") in app.screen._options
        assert app.screen.query_one("#model-picker-select", Select).expanded


def test_model_picker_uses_configured_claude_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    async def catalog():
        return (("Default", "default"), ("Opus 5.5 [claude-opus-5-5]", "opus"),
                ("Sonnet 5.5", "sonnet"), ("Custom model…", "custom"))

    monkeypatch.setattr("dennice.tui.app.load_claude_model_catalog", catalog)
    asyncio.run(_open_claude_model_picker())


async def _open_claude_model_picker() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.harness.config.executor.provider = "claude"
        app._run_slash_command("/model")
        await pilot.pause()
        assert isinstance(app.screen, ModelPickerScreen)
        assert ("Opus 5.5 [claude-opus-5-5]", "opus") in app.screen._options
        assert app.screen.query_one("#model-picker-select", Select).expanded
        assert app.screen.query_one("#model-picker-refresh", Button).display


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
        assert "Choose an executor provider" in str(app.screen.query_one("#setup-provider").render())
        assert not app.screen.query_one("#setup-copilot-note", Static).display
        assert app.screen.query_one("#setup-save", Button).disabled


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
