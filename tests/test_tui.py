import asyncio

from dennice.tui.app import DenniceApp, SetupScreen, _wordmark_renderable


def test_tui_launches_headlessly() -> None:
    asyncio.run(_launch())


def test_wordmark_uses_a_star_for_the_i_dot() -> None:
    wordmark = _wordmark_renderable().plain
    assert wordmark.count("⭐") == 1
    assert "★" not in wordmark
    assert wordmark.count("\n") == 4


async def _launch() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#home-task").value == ""
        masthead = str(app.query_one("#masthead-copy").render())
        assert "Dennice" in masthead
        assert "router:" not in masthead
        assert "⭐" in str(app.query_one("#home-title").render())
        assert app.query_one("#home-mascot")
        assert not app.query_one("#workspace").display
        assert not app.query_one("#details").display
        assert "Cognitive routing" in str(app.query_one("#routing").render())


def test_setup_page_opens(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    asyncio.run(_open_setup())


async def _open_setup() -> None:
    app = DenniceApp()
    async with app.run_test() as pilot:
        app.action_setup()
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        assert "Selected executor: Mock" in str(app.screen.query_one("#setup-provider").render())
