import asyncio

from dennice.tui.app import DenniceApp, _wordmark_renderable


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
