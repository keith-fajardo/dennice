from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image as PILImage
from rich.style import Style
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Static

from dennice import __version__
from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.runner import BenchmarkRunner
from dennice.core.config import DenniceConfig, ProviderConfig, ReasoningEffort
from dennice.core.harness import Harness
from dennice.core.models import BenchmarkMode, EventKind, Task


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
SLASH_COMMANDS = (
    ("/new", "start a fresh conversation"),
    ("/setup", "choose provider, model, and effort"),
    ("/details", "show routing and event details"),
    ("/route <task>", "classify without execution"),
    ("/run <task>", "route and execute a task"),
    ("/benchmark", "run the configured benchmark"),
    ("/help", "show all commands"),
)


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class SetupSelection:
    executor: ProviderConfig
    router: ProviderConfig
    jev_api_key_env: str
    openjev_endpoint: str
    openjev_model: str

class TaskInput(Input):
    """Task composer that gives the slash menu first access to navigation keys."""

    def on_key(self, event: events.Key) -> None:
        app = self.app
        if isinstance(app, DenniceApp) and app.handle_command_navigation(self, event):
            event.prevent_default()
            event.stop()


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
    activity = Text()
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


class SetupScreen(ModalScreen[SetupSelection | None]):
    """Configure System 1 routing and System 2 execution without secrets."""

    CSS = """
    SetupScreen { align: center middle; background: #000000aa; }
    #setup-dialog {
        width: 76;
        height: auto;
        padding: 1 2;
        border: tall #f03c95;
        background: #161616;
    }
    #setup-title { text-style: bold; color: #f3f3f3; }
    #setup-description { color: #b1b1b1; margin: 1 0; }
    #setup-provider, #setup-router { color: #ffd166; margin-top: 1; }
    #setup-model, #setup-router-model, #setup-openjev-endpoint, #setup-openjev-model { margin-top: 1; }
    #setup-buttons, #setup-router-buttons { height: 3; margin-top: 1; }
    #setup-buttons Button, #setup-router-buttons Button { margin-right: 1; }
    #setup-model-label, #setup-effort-label, #setup-router-model-label, #setup-openjev-label, #setup-jev-label { color: #d8d8d8; margin-top: 1; }
    #setup-effort-buttons { height: 3; }
    #setup-effort-buttons Button { margin-right: 1; }
    #setup-save { margin-top: 1; }
    #setup-note { color: #8e8e8e; margin-top: 1; }
    """

    def __init__(self, config: DenniceConfig) -> None:
        super().__init__()
        self.executor_provider = (
            config.executor.provider if config.executor.provider in {"mock", "codex", "claude"} else "mock"
        )
        self.executor_model = config.executor.model
        self.reasoning_effort = (
            config.executor.reasoning_effort.value if config.executor.reasoning_effort else "default"
        )
        self.router_provider = (
            config.router.provider if config.router.provider in {"rule", "codex", "jev", "openjev"} else "rule"
        )
        self.router_model = config.router.model
        self.jev_api_key_env = config.jev.api_key_env
        self.openjev_endpoint = config.openjev.endpoint
        self.openjev_model = config.openjev.model

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield Static("Dennice Setup", id="setup-title")
            yield Static(
                "Choose the System 2 executor and System 1 cognitive router. Credentials stay outside Dennice.",
                id="setup-description",
            )
            yield Static("System 2 executor", id="setup-provider")
            with Horizontal(id="setup-buttons"):
                yield Button("Mock · offline", id="setup-mock")
                yield Button("Codex CLI · ChatGPT login", id="setup-codex")
                yield Button("Claude Code · Claude login", id="setup-claude")
            yield Static("Executor model", id="setup-model-label")
            yield Input(
                value=self.executor_model,
                placeholder="Model (for example: default)",
                id="setup-model",
            )
            yield Static("Reasoning effort", id="setup-effort-label")
            with Horizontal(id="setup-effort-buttons"):
                yield Button("Default", id="effort-default")
                yield Button("Low", id="effort-low")
                yield Button("Medium", id="effort-medium")
                yield Button("High", id="effort-high")
                yield Button("XHigh", id="effort-xhigh")
            yield Static("System 1 cognitive router", id="setup-router")
            with Horizontal(id="setup-router-buttons"):
                yield Button("Rule · offline", id="router-rule")
                yield Button("Codex CLI", id="router-codex")
                yield Button("Jev · hosted", id="router-jev")
                yield Button("OpenJev · local", id="router-openjev")
            yield Static("Codex router model", id="setup-router-model-label")
            yield Input(
                value=self.router_model,
                placeholder="default",
                id="setup-router-model",
            )
            yield Static("Local OpenJev-compatible server", id="setup-openjev-label")
            yield Input(
                value=self.openjev_endpoint,
                placeholder="http://127.0.0.1:3000/v1/systemone",
                id="setup-openjev-endpoint",
            )
            yield Input(value=self.openjev_model, placeholder="openjev", id="setup-openjev-model")
            yield Static("Hosted Jev API key environment variable (name only)", id="setup-jev-label")
            yield Input(
                value=self.jev_api_key_env,
                placeholder="JEV_API_KEY",
                id="setup-jev-api-key-env",
            )
            yield Button("Save configuration", variant="primary", id="setup-save")
            yield Button("Cancel", id="setup-cancel")
            yield Static(
                "Sign in separately with `codex login` or `claude`. Local OpenJev uses no Dennice API key.",
                id="setup-note",
            )

    def on_mount(self) -> None:
        self._show_selection()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-mock":
            self.executor_provider = "mock"
            self._show_selection()
        elif event.button.id == "setup-codex":
            self.executor_provider = "codex"
            if self.query_one("#setup-model", Input).value in {"", "v1"}:
                self.query_one("#setup-model", Input).value = "default"
            self._show_selection()
        elif event.button.id == "setup-claude":
            self.executor_provider = "claude"
            if self.query_one("#setup-model", Input).value in {"", "v1"}:
                self.query_one("#setup-model", Input).value = "default"
            self._show_selection()
        elif event.button.id and event.button.id.startswith("router-"):
            self.router_provider = event.button.id.removeprefix("router-")
            self._show_selection()
        elif event.button.id and event.button.id.startswith("effort-"):
            self.reasoning_effort = event.button.id.removeprefix("effort-")
            self._show_selection()
        elif event.button.id == "setup-save":
            model = self.query_one("#setup-model", Input).value.strip() or "default"
            effort = (
                None
                if self.reasoning_effort == "default"
                else ReasoningEffort(self.reasoning_effort)
            )
            if self.executor_provider == "mock":
                model, effort = "v1", None
            env_name = self.query_one("#setup-jev-api-key-env", Input).value.strip() or "JEV_API_KEY"
            router_model = self.query_one("#setup-router-model", Input).value.strip() or "default"
            endpoint = (
                self.query_one("#setup-openjev-endpoint", Input).value.strip()
                or self.openjev_endpoint
            )
            openjev_model = self.query_one("#setup-openjev-model", Input).value.strip() or "openjev"
            if self.router_provider == "rule":
                router_model = "v1"
            elif self.router_provider == "openjev":
                router_model = openjev_model
            self.dismiss(
                SetupSelection(
                    executor=ProviderConfig(
                        provider=self.executor_provider, model=model, reasoning_effort=effort
                    ),
                    router=ProviderConfig(provider=self.router_provider, model=router_model),
                    jev_api_key_env=env_name,
                    openjev_endpoint=endpoint,
                    openjev_model=openjev_model,
                )
            )
        elif event.button.id == "setup-cancel":
            self.dismiss(None)

    def _show_selection(self) -> None:
        executor_labels = {
            "mock": "Mock (offline)",
            "codex": "Codex CLI (uses the current ChatGPT/Codex login)",
            "claude": "Claude Code (uses the current Claude subscription/login)",
        }
        label = executor_labels[self.executor_provider]
        self.query_one("#setup-provider", Static).update(f"Selected executor: {label}")
        model_selected = self.executor_provider in {"codex", "claude"}
        for widget_id in ("#setup-model-label", "#setup-model"):
            self.query_one(widget_id).display = model_selected
        for widget_id in ("#setup-effort-label", "#setup-effort-buttons"):
            self.query_one(widget_id).display = self.executor_provider == "codex"
        effort_label = "Default" if self.reasoning_effort == "default" else self.reasoning_effort.upper()
        self.query_one("#setup-effort-label", Static).update(f"Reasoning effort: {effort_label}")
        router_labels = {
            "rule": "Rule router (deterministic and offline)",
            "codex": "Codex router (System 1 only; does not solve the task)",
            "jev": "Hosted Jev router (System 1; reads its key from the environment)",
            "openjev": "OpenJev-compatible local router (typed System 1 decisions)",
        }
        self.query_one("#setup-router", Static).update(
            f"Selected router: {router_labels[self.router_provider]}"
        )
        for widget_id in ("#setup-router-model-label", "#setup-router-model"):
            self.query_one(widget_id).display = self.router_provider == "codex"
        for widget_id in (
            "#setup-openjev-label",
            "#setup-openjev-endpoint",
            "#setup-openjev-model",
        ):
            self.query_one(widget_id).display = self.router_provider == "openjev"


class DenniceApp(App[None]):
    """Thin Textual control surface over Harness.run_events()."""

    TITLE = "Dennice"
    BINDINGS = [
        ("n", "new_task", "New task"),
        ("r", "run_task", "Run task"),
        ("c", "classify_task", "Classify"),
        ("b", "run_benchmark", "Benchmark"),
        Binding("ctrl+s", "setup", "Setup", priority=True),
        Binding("ctrl+p", "command_help", "Commands", priority=True),
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
    .command-menu {
        width: 1fr;
        display: none;
        margin: 0 1;
        padding: 1;
        color: #d8d8d8;
        border: round #d94891;
        background: #171116;
    }
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
        self._conversation: list[ChatMessage] = []
        self._command_matches: list[tuple[str, str]] = []
        self._command_selection = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield self._mascot_widget("header-mascot", width=12, height=5)
            yield Static(self._masthead_text(), id="masthead-copy")
        with Vertical(id="home"):
            with Vertical(id="home-card"):
                with Horizontal(id="home-brand"):
                    yield self._mascot_widget("home-mascot", width=12, height=5)
                    yield Static(_wordmark_renderable(), id="home-title")
                yield TaskInput(
                    placeholder='Ask anything…  "Investigate a Snowflake cost increase"',
                    id="home-task",
                )
                yield Static("", id="home-command-menu", classes="command-menu")
                yield Static(
                    "Run  ·  Route  ·  Benchmark  ·  Setup",
                    id="home-modes",
                )
                yield Static(
                    "enter run   /help commands   ctrl+s setup   n new task   q quit",
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
                    yield Static("", id="output")
            yield TaskInput(
                placeholder="Describe the next task…  /help for commands",
                id="workspace-task",
            )
            yield Static("", id="workspace-command-menu", classes="command-menu")
        yield Static(self._status_text(), id="statusline")
        yield Footer(id="footer")

    def on_mount(self) -> None:
        self.query_one("#home-task", Input).focus()
        self.set_interval(0.12, self._animate_activity)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value.startswith("/"):
            self._run_slash_command(value)
        else:
            self._start_run(value)
        event.input.value = ""

    def on_input_changed(self, event: Input.Changed) -> None:
        menu_id = "#home-command-menu" if event.input.id == "home-task" else "#workspace-command-menu"
        self._show_command_menu(menu_id, event.value)

    def action_run_task(self) -> None:
        self._start_run(self._active_task_input().value)

    def action_new_task(self) -> None:
        self._conversation.clear()
        self._run_is_active = False
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

    def action_command_help(self) -> None:
        self._show_command_help()

    def action_setup(self) -> None:
        self.push_screen(
            SetupScreen(self.harness.config),
            self._apply_setup,
        )

    def _apply_setup(self, selection: SetupSelection | None) -> None:
        if selection is None:
            return
        config = self.harness.config.model_copy(deep=True)
        config.executor = selection.executor
        config.router = selection.router
        config.jev.api_key_env = selection.jev_api_key_env
        config.openjev.endpoint = selection.openjev_endpoint
        config.openjev.model = selection.openjev_model
        config.save()
        self.harness = Harness(config)
        self.query_one("#masthead-copy", Static).update(self._masthead_text())
        self.notify(f"Saved executor: {selection.executor.provider}; router: {selection.router.provider}")

    def action_toggle_details(self) -> None:
        if not self.query_one("#workspace", Vertical).display:
            return
        details = self.query_one("#details", Vertical)
        details.display = not details.display

    def _run_slash_command(self, value: str) -> None:
        command, _, argument = value[1:].partition(" ")
        command = command.lower()
        argument = argument.strip()
        if command in {"help", "commands"}:
            self._show_command_help()
        elif command == "new":
            self.action_new_task()
        elif command == "setup":
            self.action_setup()
        elif command == "details":
            self._activate_workspace("")
            details = self.query_one("#details", Vertical)
            details.display = not details.display
        elif command == "benchmark":
            self.action_run_benchmark()
        elif command == "route":
            if not argument:
                self._show_local_message("Usage: /route <task>")
            else:
                self._activate_workspace(argument)
                self._classify(argument)
        elif command == "run":
            if not argument:
                self._show_local_message("Usage: /run <task>")
            else:
                self._start_run(argument)
        else:
            self._show_local_message(f"Unknown command: /{command}\n\nType /help to see available commands.")

    def _show_command_help(self) -> None:
        commands = "\n".join(f"{command} — {description}" for command, description in SLASH_COMMANDS)
        self._show_local_message("Commands\n\n" + commands)

    def _show_command_menu(self, menu_id: str, value: str) -> None:
        menu = self.query_one(menu_id, Static)
        if not value.startswith("/"):
            menu.display = False
            self._command_matches = []
            return
        query = value.lower()
        matches = [
            (command, description)
            for command, description in SLASH_COMMANDS
            if command.startswith(query) or query == "/"
        ]
        if not matches:
            menu.display = False
            self._command_matches = []
            return
        self._command_matches = matches
        self._command_selection = 0
        self._render_command_menu(menu)
        menu.display = True

    def handle_command_navigation(self, input_widget: TaskInput, event: events.Key) -> bool:
        menu_id = "#home-command-menu" if input_widget.id == "home-task" else "#workspace-command-menu"
        menu = self.query_one(menu_id, Static)
        if not menu.display or not self._command_matches:
            return False
        if event.key == "down":
            self._command_selection = (self._command_selection + 1) % len(self._command_matches)
            self._render_command_menu(menu)
            return True
        if event.key == "up":
            self._command_selection = (self._command_selection - 1) % len(self._command_matches)
            self._render_command_menu(menu)
            return True
        if event.key != "enter":
            return False
        command, _ = self._command_matches[self._command_selection]
        if "<task>" in command:
            input_widget.value = command.split(" ", maxsplit=1)[0] + " "
            menu.display = False
            return True
        menu.display = False
        input_widget.value = ""
        self._run_slash_command(command)
        return True

    def _render_command_menu(self, menu: Static) -> None:
        rendered = Text("Commands\n", style="bold #ff83c1")
        for index, (command, description) in enumerate(self._command_matches):
            selected = index == self._command_selection
            prefix = "› " if selected else "  "
            style = "bold #fff3fa" if selected else "#c0a8b5"
            rendered.append(f"{prefix}{command:<16} {description}", style=style)
            if index < len(self._command_matches) - 1:
                rendered.append("\n")
        menu.update(rendered)

    def _show_local_message(self, message: str) -> None:
        self._activate_workspace("")
        self._conversation.append(ChatMessage("assistant", message))
        self._show_transcript()

    def _start_run(self, task: str) -> None:
        if task.strip():
            history = [
                {"role": message.role, "content": message.content}
                for message in self._conversation
                if message.content.strip()
            ]
            request = Task(
                prompt=task,
                context={"conversation_history": history} if history else {},
            )
            assistant_message = ChatMessage("assistant", "")
            self._conversation.extend((ChatMessage("user", task), assistant_message))
            self._activate_workspace(task)
            self._run(request, assistant_message)

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
    async def _run(self, task: Task, assistant_message: ChatMessage) -> None:
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
                    assistant_message.content = output
                    self._show_transcript()
                if event.kind == EventKind.RUN_FAILED:
                    self._run_is_active = False
                    error = str(event.payload.get("error", "Unknown execution failure."))
                    assistant_message.content = (
                        "Execution failed\n\n"
                        + error
                        + "\n\nThe run trace was saved locally. Press Ctrl+D to inspect its event timeline."
                    )
                    self._show_transcript()
                    self.notify("Execution failed; details are shown in the chat panel.", severity="error")
        finally:
            self._run_is_active = False

    def _animate_activity(self) -> None:
        if not self._run_is_active:
            return
        self._activity_frame += 1
        self._show_activity()

    def _show_activity(self) -> None:
        self._show_transcript()

    def _show_transcript(self) -> None:
        executor_name = self.harness.executor.id.capitalize()
        transcript = Text()
        for index, message in enumerate(self._conversation):
            label_style = "bold #81aaff" if message.role == "user" else "bold #ff83c1"
            label = "You" if message.role == "user" else "Dennice"
            transcript.append(label + "\n", style=label_style)
            if message.role == "assistant" and not message.content and self._run_is_active:
                transcript.append_text(_activity_renderable(executor_name, self._activity_frame))
            else:
                transcript.append(message.content or "…", style="#e7e7e7")
            if index < len(self._conversation) - 1:
                transcript.append("\n\n")
        output = self.query_one("#output", Static)
        output.update(transcript)
        output.scroll_end(animate=False)

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
