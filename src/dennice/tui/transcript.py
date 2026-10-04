"""Selectable terminal transcript while preserving rendered Markdown styles."""

from rich.text import Text
from rich.style import Style
from textual.binding import Binding
from textual.widgets import Static
from urllib.parse import urlsplit


class Transcript(Static, can_focus=True):
    ALLOW_SELECT = True
    BINDINGS = [Binding("ctrl+c", "copy_selection", "Copy selection", show=False),
                Binding("pageup", "page(-1)", "Previous page", show=False),
                Binding("pagedown", "page(1)", "Next page", show=False),
                Binding("home", "edge(False)", "Start", show=False),
                Binding("end", "edge(True)", "End", show=False)]

    def action_page(self, direction):
        if self.parent and self.parent.is_scrollable:
            self.parent.scroll_relative(y=direction * max(1, self.parent.content_size.height - 1), animate=False)

    def action_edge(self, end):
        if self.parent and self.parent.is_scrollable:
            if end:
                self.parent.scroll_end(animate=False)
            else:
                self.parent.scroll_home(animate=False)

    def action_open_link(self, url):
        # Only a deliberate click opens a browser. Never dispatch file://,
        # javascript: or arbitrary command-like schemes from model output.
        try:
            if not isinstance(url, str) or len(url) > 8192 or any(ord(character) < 32 for character in url):
                raise ValueError()
            parsed = urlsplit(url)
        except ValueError:
            self.app.notify("Invalid web link.", severity="warning")
            return
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            self.app.notify("Only HTTP/HTTPS web links can be opened.", severity="warning")
            return
        self.app.open_url(url)

    def render(self):
        # Textual can extract and highlight selections from Text/Content, but
        # not an arbitrary Rich Group (our Markdown transcript's old visual).
        if not self.content:
            return Text("")
        options = self.app.console_options.update_width(max(1, self.content_size.width))
        text = Text(no_wrap=True, overflow="crop")
        for segment in self.app.console.render(self.content, options):
            if not segment.control:
                style = segment.style
                if style and style.link:
                    style += Style(underline=True, meta={"@click": ("open_link", (style.link,))})
                text.append(segment.text, style=style)
        return text

    def action_copy_selection(self):
        selected = self.screen.get_selected_text()
        if selected:
            self.app.copy_to_clipboard(selected)
            self.app.notify("Selected text copied")
