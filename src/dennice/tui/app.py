from __future__ import annotations

from pathlib import Path

from PIL import Image as PILImage
from rich.style import Style
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Static

from dennice import __version__
from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.runner import BenchmarkRunner
from dennice.core.config import ProviderConfig
from dennice.core.harness import Harness
from dennice.core.models import BenchmarkMode
from dennice.core.models import EventKind


TERMINAL_MASCOT_GRID = (
    "....H..............H....",
    "...HWH............HWH...",
    "..HWPWH..........HWPWH..",
    ".HWPPWH..........HWPPWH.",
    ".....HHHHHHHHHHHHHH.....",
    "...HHHHHHHHHHHHHHHHHH...",
    "..HHHHHHHHHHHHHHHHHHHH..",
    ".HHHHHHHHHHHHHHHHHHHHHH.",
    "...HSSSSSSSSSSSSSSSSH...",
    "..HHSSSSSSSSSSSSSSSSHH..",
    ".HHHSSSSSSSSSSSSSSSSHHH.",
    "..HHSSSSSSSSSSSSSSSSHH..",
    ".HHHSSSSKKSSSSKKSSSSHHH.",
    ".HHHSSSSKKSSSSKKSSSSHHH.",
    "..HHSSSSSSSSSSSSSSSSHH..",
    "...HSSSSSSSMMSSSSSSSH...",
    "..HHSSSSSSSSSSSSSSSSHH..",
    "...HSSSSSSSSSSSSSSSSH...",
    ".....SSSSSSSSSSSSSS.....",
    "........................",
)
COMPACT_TERMINAL_MASCOT_GRID = (
    # Approved home mascot: do not alter without a new visual-design approval.
    "..H......H..",
    ".HWH....HWH.",
    ".HPPHHHHPPH.",
    ".HHHHHHHHHH.",
    ".HSSSSSSSSH.",
    "HHSSSSSSSSHH",
    "HHSKSSSSKSHH",
    ".HSSSSSSSSH.",
    "..SSSMMMSS..",
    "............",
)
TERMINAL_MASCOT_COLORS = {
    "P": (241, 139, 174),  # pink inner ears
    "H": (89, 58, 50),  # dark brown hair
    "W": (255, 235, 221),  # cream inner-ear border
    "S": (247, 208, 176),  # warm skin
    "K": (33, 27, 32),  # dot eyes
    "M": (171, 88, 99),  # tiny mouth
}
MASCOT_PALETTE = tuple(TERMINAL_MASCOT_COLORS.values())
WORDMARK_INITIAL_COLOR = "#f03c95"
WORDMARK_LETTER_COLOR = "#e9e9e9"
WORDMARK_STAR_COLOR = "#ffd166"
WORDMARK_GLYPHS = {
    "D": ("███ ", "█  █", "█  █", "█  █", "███ "),
    "e": (" ██ ", "█  █", "████", "█   ", " ██ "),
    "n": ("    ", "███ ", "█  █", "█  █", "█  █"),
    "i": ("⭐", "  ", "█ ", "█ ", "██"),
    "c": ("    ", " ███", "█   ", "█   ", " ███"),
}
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")


def _quantize(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Map arbitrary input to the intentional native terminal sprite palette."""
    return min(MASCOT_PALETTE, key=lambda swatch: sum((a - b) ** 2 for a, b in zip(color, swatch)))


def _terminal_mascot_image(grid: tuple[str, ...] = TERMINAL_MASCOT_GRID) -> PILImage.Image:
    """Create the mascot at its final logical terminal-pixel resolution."""
    width = len(grid[0])
    height = len(grid)
    image = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    for y, row in enumerate(grid):
        for x, token in enumerate(row):
            if token != ".":
                image.putpixel((x, y), (*TERMINAL_MASCOT_COLORS[token], 255))
    return image


def _mascot_renderable(width: int, height: int) -> Text:
    """Render the mascot designed against the actual terminal-cell pixel grid."""
    background = (9, 9, 9)
    target_height = height * 2
    image = _terminal_mascot_image(
        COMPACT_TERMINAL_MASCOT_GRID if width <= 12 and target_height <= 12 else TERMINAL_MASCOT_GRID
    )
    scale = min(width / image.width, target_height / image.height)
    render_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(render_size, PILImage.Resampling.NEAREST)

    canvas = PILImage.new("RGBA", (width, target_height), (0, 0, 0, 0))
    offset = ((width - render_size[0]) // 2, (target_height - render_size[1]) // 2)
    canvas.alpha_composite(image, dest=offset)
    text = Text(no_wrap=True)
    for y in range(height):
        for x in range(width):
            top_pixel = canvas.getpixel((x, y * 2))
            bottom_pixel = canvas.getpixel((x, y * 2 + 1))
            top = top_pixel[:3] if top_pixel[3] else background
            bottom = bottom_pixel[:3] if bottom_pixel[3] else background
            text.append("▀", style=Style(color=f"rgb{top}", bgcolor=f"rgb{bottom}"))
        if y < height - 1:
            text.append("\n")
    return text


def _wordmark_renderable() -> Text:
    """Draw Dennice in a compact, original terminal-pixel wordmark."""
    wordmark = Text(no_wrap=True)
    for row in range(5):
        for index, character in enumerate("Dennice"):
            glyph = WORDMARK_GLYPHS[character][row]
            color = WORDMARK_INITIAL_COLOR if index == 0 else WORDMARK_LETTER_COLOR
            glyph_color = WORDMARK_STAR_COLOR if character == "i" and row == 0 else color
            wordmark.append(glyph, style=Style(color=glyph_color, bold=True))
            if index < len("Dennice") - 1:
                wordmark.append(" ")
        if row < 4:
            wordmark.append("\n")
    return wordmark


def _activity_renderable(executor_name: str, frame: int) -> Text:
    """Render a compact spinner whose highlight travels across the status message."""
    message = f"Working with {executor_name}…"
    active = frame % len(message)
    activity = Text("Agent output\n\n")
    activity.append(f"{SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]} ", style="bold #f03c95")
    for index, character in enumerate(message):
        distance = (index - active) % len(message)
        if distance == 0:
            style = "bold #fff3fa"
        elif distance in {1, len(message) - 1}:
            style = "bold #ff83c1"
        elif distance in {2, len(message) - 2}:
            style = "#d94891"
        else:
            style = "#7d4562"
        activity.append(character, style=style)
    return activity


class SetupScreen(ModalScreen[ProviderConfig | None]):
    """Choose a local executor without collecting credentials in Dennice."""

    CSS = """
    SetupScreen { align: center middle; background: #000000aa; }
    #setup-dialog {
        width: 68;
        height: auto;
        padding: 1 2;
        border: tall #f03c95;
        background: #161616;
    }
    #setup-title { text-style: bold; color: #f3f3f3; }
    #setup-description { color: #b1b1b1; margin: 1 0; }
    #setup-provider { color: #ffd166; margin-top: 1; }
    #setup-model { margin-top: 1; }
    #setup-buttons { height: 3; margin-top: 1; }
    #setup-buttons Button { margin-right: 1; }
    #setup-save { margin-top: 1; }
    #setup-note { color: #8e8e8e; margin-top: 1; }
    """

    def __init__(self, executor: ProviderConfig) -> None:
        super().__init__()
        self.provider = executor.provider if executor.provider in {"mock", "codex"} else "mock"
        self.model = executor.model

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield Static("Dennice Setup", id="setup-title")
            yield Static(
                "Choose the System 2 executor. Dennice never stores provider credentials.",
                id="setup-description",
            )
            yield Static("", id="setup-provider")
            with Horizontal(id="setup-buttons"):
                yield Button("Mock · offline", id="setup-mock")
                yield Button("Codex CLI · ChatGPT login", id="setup-codex")
            yield Input(value=self.model, placeholder="Model (for example: default)", id="setup-model")
            yield Button("Save configuration", variant="primary", id="setup-save")
            yield Button("Cancel", id="setup-cancel")
            yield Static(
                "Codex runs with read-only permissions. Sign in separately with `codex login`.",
                id="setup-note",
            )

    def on_mount(self) -> None:
        self._show_selection()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-mock":
            self.provider = "mock"
            self._show_selection()
        elif event.button.id == "setup-codex":
            self.provider = "codex"
            self._show_selection()
        elif event.button.id == "setup-save":
            model = self.query_one("#setup-model", Input).value.strip() or "default"
            self.dismiss(ProviderConfig(provider=self.provider, model=model))
        elif event.button.id == "setup-cancel":
            self.dismiss(None)

    def _show_selection(self) -> None:
        label = "Codex CLI (uses the current Codex login)" if self.provider == "codex" else "Mock (offline)"
        self.query_one("#setup-provider", Static).update(f"Selected executor: {label}")


class DenniceApp(App[None]):
    """Thin Textual control surface over Harness.run_events()."""

    TITLE = "Dennice"
    BINDINGS = [
        ("n", "new_task", "New task"),
        ("r", "run_task", "Run task"),
        ("c", "classify_task", "Classify"),
        ("b", "run_benchmark", "Benchmark"),
        Binding("ctrl+s", "setup", "Setup", priority=True),
        Binding("ctrl+d", "toggle_details", "Details", priority=True),
        ("q", "quit", "Quit"),
    ]
    CSS = """
    Screen { layout: vertical; background: #090909; }
    #masthead {
        height: 8;
        padding: 0 2;
        align: left middle;
        background: $boost;
        color: $text;
        border-bottom: heavy $accent;
        text-style: bold;
        display: none;
    }
    #header-mascot { width: 12; height: 5; margin-right: 2; }
    #header-mascot-fallback { width: 8; content-align: center middle; color: #ff4fa3; }
    #masthead-copy { width: 1fr; }
    #home {
        height: 1fr;
        align: center middle;
    }
    #home-card {
        width: 72;
        height: auto;
        padding: 0;
        background: #090909;
        align: center top;
    }
    #home-brand {
        width: 1fr;
        /* Both parts of the lockup occupy exactly five terminal rows. */
        height: 5;
        align: center middle;
        margin-bottom: 1;
    }
    #home-title {
        width: 36;
        height: 5;
        content-align: left middle;
        margin-left: 2;
    }
    #home-mascot { width: 12; height: 5; }
    #home-mascot-fallback {
        width: 1fr;
        height: 8;
        content-align: center middle;
        text-align: center;
        color: #ff4fa3;
    }
    #home-task {
        width: 1fr;
        height: 4;
        padding: 0 1;
        background: #202020;
        color: #e7e7e7;
        border-left: heavy #4d8dff;
        border-top: none;
        border-right: none;
        border-bottom: none;
    }
    #home-modes { color: #a8a8a8; margin: 1 1 0 1; }
    #home-help { color: #767676; margin-top: 1; }
    #workspace { height: 1fr; display: none; }
    #workspace-main { height: 1fr; }
    #details { display: none; width: 38; height: 1fr; }
    #workspace-task { margin: 1 2; border: tall $accent; }
    #agent { width: 1fr; height: 1fr; }
    #output {
        height: 1fr;
        margin: 1 2;
        padding: 1 2;
        overflow: auto;
        background: #090909;
    }
    .detail-panel {
        border: round $accent;
        margin: 0 1 1 1;
        padding: 1;
        height: 1fr;
        overflow: auto;
        background: $panel;
    }
    #statusline {
        height: 1;
        padding: 0 1;
        color: #717171;
        background: #090909;
    }
    #footer { display: none; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.harness = Harness.from_config()
        self._activity_frame = 0
        self._run_is_active = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield self._mascot_widget("header-mascot", width=12, height=5)
            yield Static(self._masthead_text(), id="masthead-copy")
        with Vertical(id="home"):
            with Vertical(id="home-card"):
                with Horizontal(id="home-brand"):
                    yield self._mascot_widget("home-mascot", width=12, height=5)
                    yield Static(_wordmark_renderable(), id="home-title")
                yield Input(
                    placeholder='Ask anything…  "Investigate a Snowflake cost increase"',
                    id="home-task",
                )
                yield Static(
                    "Run  ·  Route  ·  Benchmark  ·  Setup",
                    id="home-modes",
                )
                yield Static(
                    "enter run   c classify   b benchmark   ctrl+s setup   n new task   q quit",
                    id="home-help",
                )
        with Vertical(id="workspace"):
            with Horizontal(id="workspace-main"):
                with Vertical(id="details"):
                    with Vertical(classes="detail-panel"):
                        yield Static("Cognitive routing\nAwaiting task.", id="routing")
                    with Vertical(classes="detail-panel"):
                        yield Static("Tools / events\nAwaiting task.", id="events")
                with Vertical(id="agent"):
                    yield Static("Agent output\nAwaiting task.", id="output")
            yield Input(
                placeholder="Describe another task…  Press Enter to route and run",
                id="workspace-task",
            )
        yield Static(self._status_text(), id="statusline")
        yield Footer(id="footer")

    def on_mount(self) -> None:
        self.query_one("#home-task", Input).focus()
        self.set_interval(0.12, self._animate_activity)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._start_run(event.value)

    def action_run_task(self) -> None:
        self._start_run(self._active_task_input().value)

    def action_new_task(self) -> None:
        self.query_one("#workspace", Vertical).display = False
        self.query_one("#home", Vertical).display = True
        task = self.query_one("#home-task", Input)
        task.value = ""
        task.focus()

    def action_classify_task(self) -> None:
        task = self._active_task_input().value
        if task.strip():
            self._activate_workspace(task)
            self._classify(task)

    def action_run_benchmark(self) -> None:
        self._activate_workspace("")
        self._benchmark()

    def action_setup(self) -> None:
        self.push_screen(SetupScreen(self.harness.config.executor), self._apply_setup)

    def _apply_setup(self, executor: ProviderConfig | None) -> None:
        if executor is None:
            return
        config = self.harness.config.model_copy(deep=True)
        config.executor = executor
        config.save()
        self.harness = Harness(config)
        self.query_one("#masthead-copy", Static).update(self._masthead_text())
        self.notify(f"Saved executor: {executor.provider} ({executor.model})")

    def action_toggle_details(self) -> None:
        if not self.query_one("#workspace", Vertical).display:
            return
        details = self.query_one("#details", Vertical)
        details.display = not details.display

    def _start_run(self, task: str) -> None:
        if task.strip():
            self._activate_workspace(task)
            self._run(task)

    def _active_task_input(self) -> Input:
        if self.query_one("#workspace", Vertical).display:
            return self.query_one("#workspace-task", Input)
        return self.query_one("#home-task", Input)

    def _activate_workspace(self, task: str) -> None:
        self.query_one("#home", Vertical).display = False
        self.query_one("#workspace", Vertical).display = True
        self.query_one("#masthead", Horizontal).display = True
        self.query_one("#footer", Footer).display = True
        workspace_task = self.query_one("#workspace-task", Input)
        workspace_task.value = task
        workspace_task.focus()

    @work(exclusive=True)
    async def _classify(self, task: str) -> None:
        if not task.strip():
            return
        decision = await self.harness.classify(task)
        self._show_routing(decision)

    @work(exclusive=True)
    async def _run(self, task: str) -> None:
        if not task.strip():
            return
        self.query_one("#events", Static).update("Tools / events\nStarting run...")
        self._run_is_active = True
        self._activity_frame = 0
        self._show_activity()
        events: list[str] = []
        output = ""
        try:
            async for event in self.harness.run_events(task):
                events.append(event.kind.value)
                self.query_one("#events", Static).update("Tools / events\n" + "\n".join(events))
                if event.kind == EventKind.ROUTING_COMPLETED:
                    self._show_routing_payload(event.payload["decision"])
                if event.kind == EventKind.MODEL_STREAM:
                    self._run_is_active = False
                    output += str(event.payload["text"])
                    self.query_one("#output", Static).update("Agent output\n" + output)
                if event.kind == EventKind.RUN_FAILED:
                    self._run_is_active = False
                    error = str(event.payload.get("error", "Unknown execution failure."))
                    self.query_one("#output", Static).update(
                        "Execution failed\n\n"
                        + error
                        + "\n\nThe run trace was saved locally. Press Ctrl+D to inspect its event timeline."
                    )
                    self.notify("Execution failed; details are shown in the chat panel.", severity="error")
        finally:
            self._run_is_active = False

    def _animate_activity(self) -> None:
        if not self._run_is_active:
            return
        self._activity_frame += 1
        self._show_activity()

    def _show_activity(self) -> None:
        executor_name = self.harness.executor.id.capitalize()
        self.query_one("#output", Static).update(_activity_renderable(executor_name, self._activity_frame))

    @work(exclusive=True)
    async def _benchmark(self) -> None:
        self.query_one("#events", Static).update("Tools / events\nBenchmark started...")
        dataset = BenchmarkDataset.load(self.harness.config.benchmark.path)
        if not dataset.items:
            dataset = BenchmarkDataset.builtin()
        result = await BenchmarkRunner(self.harness).run(dataset, BenchmarkMode.ROUTER)
        metrics = result.aggregate_routing
        text = (
            f"Benchmark results\nMode: {result.mode.value}\nTasks: {len(result.items)}\n\n"
            f"Primary accuracy: {metrics.primary_accuracy if metrics.primary_accuracy is not None else '-'}\n"
            f"Multilabel F1: {metrics.multilabel_f1 if metrics.multilabel_f1 is not None else '-'}"
        )
        self.query_one("#output", Static).update(text)
        self.query_one("#events", Static).update("Tools / events\nbenchmark completed")

    def _masthead_text(self) -> str:
        return (
            "Dennice\n"
            "Cognitive Data Agent Harness\n"
            f"executor: {self.harness.executor.id}-{self.harness.executor.version}"
        )

    @staticmethod
    def _status_text() -> str:
        return f"{Path.cwd()}".replace(str(Path.home()), "~") + f"{' ' * 4}{__version__}"

    @staticmethod
    def _mascot_widget(image_id: str, *, width: int, height: int) -> Static:
        return Static(_mascot_renderable(width, height), id=image_id)

    def _show_routing(self, decision: object) -> None:
        self._show_routing_payload(decision.model_dump(mode="json"))  # type: ignore[attr-defined]

    def _show_routing_payload(self, decision: dict[str, object]) -> None:
        lines = [f"Cognitive routing\nTask family: {decision['task_family']}", ""]
        primary = decision["primary_demand"]
        supporting = decision["supporting_demands"]
        for score in decision["cognitive_demands"]:  # type: ignore[index]
            item = score  # type: ignore[assignment]
            label = "PRIMARY" if item["demand"] == primary else "SUPPORTING" if item["demand"] in supporting else ""
            lines.append(f"{item['demand']:<27} {item['confidence']:.2f} {label}")
        self.query_one("#routing", Static).update("\n".join(lines))
