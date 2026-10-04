"""Offline, multi-step terminal journeys: no provider or extension launches."""

import asyncio

from textual.widgets import Button

from dennice.core.config import DenniceConfig, PermissionMode, ProviderConfig
from dennice.core.models import EventKind
from dennice.tui.app import DenniceApp, SetupScreen, TaskComposer


async def submit(app, pilot, text):
    composer = app._active_task_input()
    composer.value = text
    composer.focus()
    await pilot.pause()
    await pilot.press("ctrl+enter")
    await pilot.pause()
    if app._execution_worker is not None:
        await app._execution_worker.wait()
    await pilot.pause()


def test_offline_chat_session_restart_journey(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def no_process(*args, **kwargs):
        raise AssertionError("Offline smoke test must not launch a provider or extension")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)

    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            assert app.query_one("#home").display
            await submit(app, pilot, "hello")
            first = app._ensure_active_session()
            saved_id = first.id
            assert first.messages[-2].content == "hello"
            assert "offline executor" in first.messages[-1].content
            trace = app.harness._last_trace
            assert trace.status == "unverified"
            assert trace.verification["verified"] is False
            assert any(event.kind == EventKind.VERIFICATION_COMPLETED for event in trace.events)
            await submit(app, pilot, "/rename Smoke session")
            assert first.title == "Smoke session"
            await pilot.press("ctrl+n")
            await pilot.pause()
            assert app._ensure_active_session().id != saved_id
            app._select_session(0)
            await submit(app, pilot, "What next?")
            assert sum(message.role == "user" for message in first.messages) == 2
            app._close_session(0)
        restarted = DenniceApp()
        async with restarted.run_test(size=(120, 45)) as pilot:
            assert restarted.query_one("#home").display
            await submit(restarted, pilot, f"/resume {saved_id}")
            session = restarted._ensure_active_session()
            assert session.title == "Smoke session"
            assert sum(message.role == "assistant" for message in session.messages) == 2
    asyncio.run(journey())


def test_setup_permission_profiles_save_and_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = DenniceConfig(executor=ProviderConfig(provider="claude", model="default"))
    config.save()

    async def catalog():
        return (("Default", "default"), ("Custom", "custom"))

    monkeypatch.setattr("dennice.tui.app.load_claude_model_catalog", catalog)

    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 55)) as pilot:
            app.action_setup()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SetupScreen)
            assert len(screen.query("#setup-mock")) == 0
            assert "Claude Code" in str(screen.query_one("#setup-provider").render())
            for widget in ("permission-read-only", "permission-workspace-write", "permission-plan"):
                assert screen.query_one(f"#{widget}").display
            screen.query_one("#permission-workspace-write", Button).press()
            await pilot.pause()
            screen.query_one("#setup-save", Button).press()
            await pilot.pause()
            assert app.harness.config.executor.permission_mode == PermissionMode.WORKSPACE_WRITE
        restarted = DenniceApp()
        assert restarted.harness.config.executor.provider == "claude"
        assert restarted.harness.config.executor.permission_mode == PermissionMode.WORKSPACE_WRITE
    asyncio.run(journey())


def test_shadow_routing_and_goal_completion_journey(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = DenniceConfig()
    config.verification.required_files = ["README.md"]
    config.tools.root = str(tmp_path)
    config.save()
    # A real pre-existing file is deliberately only an existence check, not
    # evidence of model task quality. No shell verifier is launched.
    from pathlib import Path
    fixture = Path(__file__).resolve().parents[1] / "README.md"
    import shutil
    shutil.copyfile(fixture, tmp_path / "README.md")

    async def journey():
        app = DenniceApp()
        async with app.run_test(size=(120, 45)) as pilot:
            await submit(app, pilot, "/routing shadow")
            assert app.harness.config.routing.mode == "shadow"
            await submit(app, pilot, "/goal Inspect the existing README")
            session = app._ensure_active_session()
            goal = app._goals.get(session.goal_id)
            assert goal.status == "complete"
            assert len(goal.runs) == 1
            trace = await app.harness.store.get(goal.runs[0])
            assert trace.verification["passed"]
            assert trace.route_plan.mode == "shadow"
    asyncio.run(journey())
