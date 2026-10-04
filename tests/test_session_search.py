import asyncio

from textual.widgets import Input, OptionList, Static

from dennice.runs.sessions import ChatMessage, ChatSession
from dennice.tui.app import DenniceApp, SessionPickerScreen


def test_live_session_regex_search_titles_content_and_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def check():
        sessions = [ChatSession.new("Billing report"), ChatSession.new("Archived notes"), ChatSession.new("Other")]
        sessions[1].closed = True
        sessions[1].messages.append(ChatMessage("assistant", "Investigate Snowflake cost spike"))
        chosen = []
        app = DenniceApp()
        async with app.run_test() as pilot:
            screen = SessionPickerScreen(sessions, None)
            app.push_screen(screen, chosen.append)
            await pilot.pause()
            search = screen.query_one("#session-picker-search", Input)
            options = screen.query_one("#session-picker-options", OptionList)
            assert screen.focused is search
            search.value = "billing|snowflake.*spike"
            await pilot.pause()
            assert options.option_count == 2
            assert options.get_option_at_index(1).id == sessions[1].id
            search.value = "no-such-session"
            await pilot.pause()
            assert options.option_count == 0
            await pilot.press("enter")
            assert app.screen is screen
            search.value = "["
            await pilot.pause()
            assert "Invalid regex" in str(screen.query_one("#session-picker-status", Static).render())
            assert options.option_count == 0
            search.value = ""
            await pilot.pause()
            assert options.option_count == 3
            await pilot.press("down", "enter")
            await pilot.pause()
            assert chosen == [sessions[1].id]
    asyncio.run(check())


def test_session_search_pathological_regex_does_not_freeze(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def check():
        session = ChatSession.new("Long text")
        session.messages.append(ChatMessage("assistant", "a" * 100000 + "!"))
        app = DenniceApp()
        async with app.run_test() as pilot:
            screen = SessionPickerScreen([session], None)
            app.push_screen(screen)
            await pilot.pause()
            screen.query_one("#session-picker-search", Input).value = "(a+)+$"
            await pilot.pause()
            assert "timed out" in str(screen.query_one("#session-picker-status", Static).render())
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not screen
    asyncio.run(check())


def test_opening_page_live_regex_titles_contents_and_keyboard_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from dennice.runs.sessions import SessionStore
    store = SessionStore(tmp_path / ".dennice/runs")
    first, second = ChatSession.new("Billing report"), ChatSession.new("Archived notes")
    second.closed = True
    second.messages.append(ChatMessage("assistant", "Snowflake cost spike"))
    store.save(first)
    store.save(second)
    async def check():
        app = DenniceApp()
        async with app.run_test(size=(120, 50)) as pilot:
            assert app.query_one("#home").display
            search = app.query_one("#home-session-search", Input)
            listing = app.query_one("#home-session-list", OptionList)
            search.value = "billing|snowflake.*spike"
            await pilot.pause()
            assert listing.option_count == 2
            search.value = "snowflake.*spike"
            await pilot.pause()
            assert app._home_session_ids == [second.id]
            search.value = "["
            await pilot.pause()
            assert listing.option_count == 0
            assert "Invalid regex" in str(app.query_one("#home-session-search-status", Static).content)
            search.value = "snowflake"
            search.focus()
            await pilot.pause()
            await pilot.press("ctrl+a")
            assert search.selected_text == "snowflake"
            await pilot.press("enter")
            await pilot.pause()
            assert app._ensure_active_session().id == second.id
            assert not app._ensure_active_session().closed
    asyncio.run(check())
