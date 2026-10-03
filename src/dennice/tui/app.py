from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path

from PIL import Image as PILImage
from rich.style import Style
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Select, Static, TextArea

from dennice import __version__
from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.runner import BenchmarkRunner
from dennice.core.config import DenniceConfig, PermissionMode, ProviderConfig, ReasoningEffort
from dennice.core.harness import Harness
from dennice.core.models import BenchmarkMode, EventKind, Task
from dennice.core.process import command_for_platform
from dennice.core.verification import verify_executor, verify_router


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
EXECUTOR_MODELS: dict[str, tuple[tuple[str, str], ...]] = {
    # These are convenient, current defaults rather than an entitlement claim:
    # users can always choose Custom for a model exposed by their local CLI.
    "codex": (
        ("Use Codex recommended default", "default"),
        ("GPT-6.1 Sol", "gpt-6.1-sol"),
        ("GPT-6 Sol", "gpt-6-sol"),
        ("GPT-6 Luna", "gpt-6-luna"),
        ("Astra", "gpt-6-astra"),
        ("GPT-5.6 Sol", "gpt-5.6-sol"),
        ("GPT-5.6 Terra", "gpt-5.6-terra"),
        ("GPT-5.6 Luna", "gpt-5.6-luna"),
        ("GPT-5.5", "gpt-5.5"),
        ("Custom model…", "custom"),
    ),
    "claude": (
        ("Use Claude Code default", "default"),
        ("Claude Sonnet", "sonnet"),
        ("Claude Opus", "opus"),
        ("Claude Haiku", "haiku"),
        ("Custom model…", "custom"),
    ),
}
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")
SLASH_COMMANDS = (
    ("/new", "start a fresh conversation"),
    ("/setup", "choose provider, model, effort, and permissions"),
    ("/config", "show executor, model, effort, and permissions"),
    ("/model <name>", "set the executor model"),
    ("/effort <level>", "set low, medium, high, xhigh, or default"),
    ("/permissions <mode>", "set read-only, workspace-write, or plan"),
    ("/session <number>", "switch to an open session tab"),
    ("/sessions", "list open sessions"),
    ("/rename <title>", "rename the current session"),
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
class ChatSession:
    id: str
    title: str
    messages: list[ChatMessage]
    input_history: list[str]


@dataclass
class SetupSelection:
    executor: ProviderConfig
    router: ProviderConfig
    permission_mode: PermissionMode | None
    jev_api_key_env: str
    openjev_endpoint: str
    openjev_model: str

class TaskComposer(TextArea):
    """Multiline task composer; Ctrl+Enter submits and Enter inserts a newline."""

    @property
    def value(self) -> str:
        """Match Input's small interface while retaining TextArea editing semantics."""
        return self.text

    @value.setter
    def value(self, value: str) -> None:
        self.load_text(value)

    def on_key(self, event: events.Key) -> None:
        app = self.app
        if not isinstance(app, DenniceApp):
            return
        if event.key == "ctrl+a":
            self.select_all()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+enter":
            app.submit_composer(self)
            event.prevent_default()
            event.stop()
        elif app.handle_command_navigation(self, event):
            event.prevent_default()
            event.stop()
        elif event.key == "up" and self.cursor_at_first_line and app.recall_composer_history(self, -1):
            event.prevent_default()
            event.stop()
        elif event.key == "down" and self.cursor_at_last_line and app.recall_composer_history(self, 1):
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
        max-height: 90%;
        padding: 1 2;
        border: tall #f03c95;
        background: #161616;
        overflow-y: auto;
    }
    #setup-title { text-style: bold; color: #f3f3f3; }
    #setup-description { color: #b1b1b1; margin: 1 0; }
    #setup-provider, #setup-router { color: #ffd166; margin-top: 1; }
    #setup-model-choice, #setup-model-custom, #setup-refresh-codex-models, #setup-router-model, #setup-openjev-endpoint, #setup-openjev-model { margin-top: 1; }
    #setup-buttons, #setup-router-buttons { height: 1; margin-top: 1; }
    #setup-buttons Button, #setup-router-buttons Button { width: 1fr; margin-right: 1; }
    #setup-model-label, #setup-effort-label, #setup-router-model-label, #setup-openjev-label, #setup-jev-label { color: #d8d8d8; margin-top: 1; }
    #setup-effort-buttons { height: 1; }
    #setup-effort-buttons Button { width: 1fr; min-width: 0; margin-right: 1; padding: 0; }
    #setup-permission-label { color: #d8d8d8; margin-top: 1; }
    #setup-permission-buttons { height: 1; }
    #setup-permission-buttons Button { margin-right: 1; }
    #setup-dialog Button { height: 1; min-height: 1; padding: 0 1; }
    #setup-test-executor, #setup-test-router {
        margin-top: 1;
        width: 34;
        background: #34557a;
        color: #ffffff;
        text-style: bold;
    }
    #setup-test-executor:hover, #setup-test-router:hover {
        background: #466e99;
    }
    #setup-test-executor:focus, #setup-test-router:focus {
        background: #466e99;
        text-style: bold underline;
    }
    #setup-test-executor:disabled, #setup-test-router:disabled {
        background: #2b4058;
        color: #c5d5e8;
        text-opacity: 1;
    }
    #setup-executor-test-result, #setup-router-test-result { color: #b8b8b8; margin-top: 1; height: auto; }
    #setup-save { margin-top: 2; width: 26; }
    #setup-cancel { margin-top: 1; width: 26; }
    #setup-note { color: #8e8e8e; margin-top: 1; }
    """

    def __init__(self, config: DenniceConfig) -> None:
        super().__init__()
        self.executor_provider = (
            config.executor.provider if config.executor.provider in {"mock", "codex", "claude"} else "mock"
        )
        self.executor_model = config.executor.model
        self._codex_model_options = EXECUTOR_MODELS["codex"]
        self.reasoning_effort = (
            config.executor.reasoning_effort.value if config.executor.reasoning_effort else "default"
        )
        self.permission_mode = config.executor.permission_mode
        self.router_provider = (
            config.router.provider if config.router.provider in {"rule", "codex", "jev", "openjev"} else "rule"
        )
        self.router_model = config.router.model
        self.jev_api_key_env = config.jev.api_key_env
        self.openjev_endpoint = config.openjev.endpoint
        self.openjev_model = config.openjev.model
        self._connection_tests: dict[str, str] = {}
        self._connection_frame = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield Static("Dennice Setup", id="setup-title")
            yield Static(
                "Choose two independent providers: the executor answers your task; the router selects its reasoning policies. Changing one does not change the other.",
                id="setup-description",
            )
            yield Static("System 2 executor", id="setup-provider")
            with Horizontal(id="setup-buttons"):
                yield Button("Mock", id="setup-mock", compact=True)
                yield Button("Codex", id="setup-codex", compact=True)
                yield Button("Claude", id="setup-claude", compact=True)
            yield Static("Executor model", id="setup-model-label")
            yield Select(
                self._model_options(),
                value=self._initial_model_choice(),
                allow_blank=False,
                id="setup-model-choice",
            )
            yield Input(value=self._initial_custom_model(), placeholder="Custom model name", id="setup-model-custom")
            yield Button("Refresh all available Codex models", id="setup-refresh-codex-models", compact=True)
            yield Static("Execution effort", id="setup-effort-label")
            with Horizontal(id="setup-effort-buttons"):
                yield Button("Default", id="effort-default", compact=True)
                yield Button("Low", id="effort-low", compact=True)
                yield Button("Medium", id="effort-medium", compact=True)
                yield Button("High", id="effort-high", compact=True)
                yield Button("XHigh", id="effort-xhigh", compact=True)
            yield Static("Permission mode", id="setup-permission-label")
            with Horizontal(id="setup-permission-buttons"):
                yield Button("Read only", id="permission-read-only", compact=True)
                yield Button("Workspace write", id="permission-workspace-write", compact=True)
                yield Button("Plan", id="permission-plan", compact=True)
            yield Button("Test executor", id="setup-test-executor", compact=True)
            yield Static("", id="setup-executor-test-result")
            yield Static("System 1 cognitive router", id="setup-router")
            with Horizontal(id="setup-router-buttons"):
                yield Button("Rule", id="router-rule", compact=True)
                yield Button("Codex", id="router-codex", compact=True)
                yield Button("Jev", id="router-jev", compact=True)
                yield Button("OpenJev", id="router-openjev", compact=True)
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
            yield Button("Test router", id="setup-test-router", compact=True)
            yield Static("", id="setup-router-test-result")
            yield Button("Save configuration", variant="primary", id="setup-save", compact=True)
            yield Button("Cancel", id="setup-cancel", compact=True)

    def on_mount(self) -> None:
        self._show_selection()
        self.set_interval(0.12, self._animate_connection_tests)
        if self.executor_provider == "codex":
            self._load_codex_model_catalog()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-mock":
            self.executor_provider = "mock"
            self._show_selection()
        elif event.button.id == "setup-codex":
            self.executor_provider = "codex"
            if self.permission_mode not in {
                PermissionMode.READ_ONLY,
                PermissionMode.WORKSPACE_WRITE,
            }:
                self.permission_mode = PermissionMode.READ_ONLY
            self.executor_model = "default"
            self._refresh_model_choices()
            self._show_selection()
            self._load_codex_model_catalog()
        elif event.button.id == "setup-claude":
            self.executor_provider = "claude"
            self.permission_mode = PermissionMode.PLAN
            self.executor_model = "default"
            self._refresh_model_choices()
            self._show_selection()
        elif event.button.id and event.button.id.startswith("router-"):
            self.router_provider = event.button.id.removeprefix("router-")
            if self.router_provider == "codex" and self.router_model in {"", "v1"}:
                self.router_model = "default"
                self.query_one("#setup-router-model", Input).value = "default"
            self._show_selection()
        elif event.button.id and event.button.id.startswith("effort-"):
            self.reasoning_effort = event.button.id.removeprefix("effort-")
            self._show_selection()
        elif event.button.id and event.button.id.startswith("permission-"):
            self.permission_mode = PermissionMode(event.button.id.removeprefix("permission-"))
            self._show_selection()
        elif event.button.id == "setup-refresh-codex-models":
            self._load_codex_model_catalog()
        elif event.button.id == "setup-test-executor":
            self._test_connection("executor")
        elif event.button.id == "setup-test-router":
            self._test_connection("router")
        elif event.button.id == "setup-save":
            self.dismiss(self._selection_from_fields())
        elif event.button.id == "setup-cancel":
            self.dismiss(None)

    def _show_selection(self) -> None:
        executor_labels = {
            "mock": "Mock (offline)",
            "codex": "Codex CLI (uses the current ChatGPT/Codex login)",
            "claude": "Claude Code (uses the current Claude subscription/login)",
        }
        label = executor_labels[self.executor_provider]
        self.query_one("#setup-provider", Static).update(f"System 2 executor: {label}")
        self.query_one("#setup-test-executor", Button).label = f"Test executor · {self.executor_provider.title()}"
        self.query_one("#setup-test-router", Button).label = f"Test router · {self.router_provider.title()}"
        model_selected = self.executor_provider in {"codex", "claude"}
        for widget_id in ("#setup-model-label", "#setup-model-choice"):
            self.query_one(widget_id).display = model_selected
        model_choice = str(self.query_one("#setup-model-choice", Select).value)
        self.query_one("#setup-model-custom", Input).display = model_selected and model_choice == "custom"
        self.query_one("#setup-refresh-codex-models", Button).display = self.executor_provider == "codex"
        for widget_id in ("#setup-effort-label", "#setup-effort-buttons"):
            self.query_one(widget_id).display = model_selected
        for widget_id in ("#setup-permission-label", "#setup-permission-buttons"):
            self.query_one(widget_id).display = model_selected
        effort_label = "Default" if self.reasoning_effort == "default" else self.reasoning_effort.upper()
        effort_note = (
            "native Codex setting"
            if self.executor_provider == "codex"
            else "Dennice prompt guidance; Claude Code has no standard CLI effort flag"
        )
        self.query_one("#setup-effort-label", Static).update(
            f"Execution effort: {effort_label} ({effort_note})"
        )
        self._set_button_selection(
            {
                "setup-mock": self.executor_provider == "mock",
                "setup-codex": self.executor_provider == "codex",
                "setup-claude": self.executor_provider == "claude",
                "router-rule": self.router_provider == "rule",
                "router-codex": self.router_provider == "codex",
                "router-jev": self.router_provider == "jev",
                "router-openjev": self.router_provider == "openjev",
                "permission-read-only": self.permission_mode == PermissionMode.READ_ONLY,
                "permission-workspace-write": self.permission_mode == PermissionMode.WORKSPACE_WRITE,
                "permission-plan": self.permission_mode == PermissionMode.PLAN,
                **{
                    f"effort-{level}": self.reasoning_effort == level
                    for level in ("default", "low", "medium", "high", "xhigh")
                },
            }
        )
        router_labels = {
            "rule": "Rule router (deterministic and offline)",
            "codex": "Codex router (System 1 only; does not solve the task)",
            "jev": "Hosted Jev router (System 1; reads its key from the environment)",
            "openjev": "OpenJev-compatible local router (typed System 1 decisions)",
        }
        self.query_one("#setup-router", Static).update(f"System 1 router: {router_labels[self.router_provider]}")
        for widget_id in ("#setup-router-model-label", "#setup-router-model"):
            self.query_one(widget_id).display = self.router_provider == "codex"
        for widget_id in (
            "#setup-openjev-label",
            "#setup-openjev-endpoint",
            "#setup-openjev-model",
        ):
            self.query_one(widget_id).display = self.router_provider == "openjev"
        for widget_id in ("#setup-jev-label", "#setup-jev-api-key-env"):
            self.query_one(widget_id).display = self.router_provider == "jev"

        permission_label = (
            "Plan (Claude Code safe planning mode)"
            if self.executor_provider == "claude"
            else (self.permission_mode.value if self.permission_mode else "Read only")
        )
        self.query_one("#setup-permission-label", Static).update(
            f"Permission mode: {permission_label}"
        )
        for button_id, mode in (
            ("#permission-read-only", PermissionMode.READ_ONLY),
            ("#permission-workspace-write", PermissionMode.WORKSPACE_WRITE),
            ("#permission-plan", PermissionMode.PLAN),
        ):
            self.query_one(button_id, Button).display = (
                (self.executor_provider == "codex" and mode != PermissionMode.PLAN)
                or (self.executor_provider == "claude" and mode == PermissionMode.PLAN)
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "setup-model-choice":
            return
        if str(event.value) != "custom":
            self.executor_model = str(event.value)
        self._show_selection()

    def _model_options(self) -> tuple[tuple[str, str], ...]:
        if self.executor_provider == "codex":
            return self._codex_model_options
        return EXECUTOR_MODELS.get(self.executor_provider, (("Use default", "default"),))

    def _selection_from_fields(self) -> SetupSelection:
        model_choice = str(self.query_one("#setup-model-choice", Select).value)
        custom_model = self.query_one("#setup-model-custom", Input).value.strip()
        model = custom_model if model_choice == "custom" else model_choice
        model = model or "default"
        effort = None if self.reasoning_effort == "default" else ReasoningEffort(self.reasoning_effort)
        if self.executor_provider == "mock":
            model, effort, permission_mode = "v1", None, None
        else:
            permission_mode = self.permission_mode
        env_name = self.query_one("#setup-jev-api-key-env", Input).value.strip() or "JEV_API_KEY"
        router_model = self.query_one("#setup-router-model", Input).value.strip() or "default"
        endpoint = self.query_one("#setup-openjev-endpoint", Input).value.strip() or self.openjev_endpoint
        openjev_model = self.query_one("#setup-openjev-model", Input).value.strip() or "openjev"
        if self.router_provider == "rule":
            router_model = "v1"
        elif self.router_provider == "openjev":
            router_model = openjev_model
        return SetupSelection(
            executor=ProviderConfig(
                provider=self.executor_provider,
                model=model,
                reasoning_effort=effort,
                permission_mode=permission_mode,
            ),
            router=ProviderConfig(provider=self.router_provider, model=router_model),
            permission_mode=permission_mode,
            jev_api_key_env=env_name,
            openjev_endpoint=endpoint,
            openjev_model=openjev_model,
        )

    @work(group="connection-tests", exclusive=True)
    async def _test_connection(self, component: str) -> None:
        """Test only the chosen component, using a snapshot of unsaved fields."""
        button = self.query_one(f"#setup-test-{component}", Button)
        result = self.query_one(f"#setup-{component}-test-result", Static)
        selection = self._selection_from_fields()
        provider = getattr(selection, component).provider
        self._connection_tests[component] = provider
        button.disabled = True
        button.label = f"Testing {provider.title()}…"
        result.update(f"Checking {provider.title()} {component} with the selections above…")
        config = self.app.harness.config.model_copy(deep=True)  # type: ignore[attr-defined]
        config.executor = selection.executor
        config.router = selection.router
        config.jev.api_key_env = selection.jev_api_key_env
        config.openjev.endpoint = selection.openjev_endpoint
        config.openjev.model = selection.openjev_model
        try:
            check = await (verify_router(config) if component == "router" else verify_executor(config))
            status = Text()
            status.append(
                "✓ Success" if check.ok else "✗ Failed",
                style="bold #79dc9b" if check.ok else "bold #ff8585",
            )
            status.append(
                f" · {provider.title()} {component}\n{check.detail}",
                style="#d8d8d8",
            )
            result.update(status)
        finally:
            self._connection_tests.pop(component, None)
            button.disabled = False
            current_provider = getattr(self, f"{component}_provider")
            button.label = f"Test {component} · {current_provider.title()}"

    def _animate_connection_tests(self) -> None:
        if not self._connection_tests:
            return
        self._connection_frame += 1
        for component, provider in self._connection_tests.items():
            self.query_one(f"#setup-{component}-test-result", Static).update(
                _activity_renderable(f"{provider.title()} {component}", self._connection_frame)
            )

    def _initial_model_choice(self) -> str:
        if self.executor_provider not in EXECUTOR_MODELS:
            return "default"
        values = {value for _, value in self._model_options()}
        return self.executor_model if self.executor_model in values else "custom"

    def _initial_custom_model(self) -> str:
        return "" if self._initial_model_choice() != "custom" else self.executor_model

    def _refresh_model_choices(self) -> None:
        selector = self.query_one("#setup-model-choice", Select)
        selector.set_options(self._model_options())
        selector.value = self._initial_model_choice()
        self.query_one("#setup-model-custom", Input).value = self._initial_custom_model()

    @work(exclusive=True)
    async def _load_codex_model_catalog(self) -> None:
        """Ask the installed, signed-in Codex CLI which models it can expose."""
        button = self.query_one("#setup-refresh-codex-models", Button)
        selector = self.query_one("#setup-model-choice", Select)
        button.disabled = True
        button.label = "Loading Codex model catalog…"
        selector.disabled = True
        command = command_for_platform(
            ["codex", "debug", "models"], "win32" if os.name == "nt" else "posix"
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            if process.returncode != 0:
                raise RuntimeError(stderr.decode("utf-8", errors="replace").strip() or "Codex returned an error")
            payload = json.loads(stdout.decode("utf-8"))
            models = payload.get("models", [])
            options = [
                (str(model.get("display_name") or model["slug"]), str(model["slug"]))
                for model in models
                if model.get("slug") and model.get("visibility", "list") == "list"
            ]
            if not options:
                raise RuntimeError("The installed Codex CLI returned no selectable models.")
            options.sort(key=lambda option: option[0].lower())
            self._codex_model_options = tuple(
                [("Use Codex recommended default", "default"), *options, ("Custom model…", "custom")]
            )
            self._refresh_model_choices()
            self.notify(f"Loaded {len(options)} Codex models from the installed CLI.")
        except (FileNotFoundError, OSError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            self.notify(f"Could not load Codex models: {error}", severity="warning")
        finally:
            selector.disabled = False
            button.disabled = False
            button.label = "Refresh all available Codex models"

    def _set_button_selection(self, selected: dict[str, bool]) -> None:
        """Give the selected provider an explicit marker, independent of keyboard focus."""
        labels = {
            "setup-mock": "Mock",
            "setup-codex": "Codex",
            "setup-claude": "Claude",
            "router-rule": "Rule",
            "router-codex": "Codex",
            "router-jev": "Jev",
            "router-openjev": "OpenJev",
            "permission-read-only": "Read only",
            "permission-workspace-write": "Workspace write",
            "permission-plan": "Plan",
            "effort-default": "Default",
            "effort-low": "Low",
            "effort-medium": "Medium",
            "effort-high": "High",
            "effort-xhigh": "XHigh",
        }
        for button_id, is_selected in selected.items():
            button = self.query_one(f"#{button_id}", Button)
            button.label = ("✓ " if is_selected else "") + labels[button_id]
            button.variant = "success" if is_selected else "default"


class ModelPickerScreen(ModalScreen[str | None]):
    """Small, keyboard-friendly model picker for the `/model` command."""

    CSS = """
    ModelPickerScreen { align: center middle; background: #000000aa; }
    #model-picker { width: 58; padding: 1 2; border: tall #f03c95; background: #161616; }
    #model-picker-title { color: #f3f3f3; text-style: bold; }
    #model-picker-note { color: #aaa; margin: 1 0; }
    #model-picker-select, #model-picker Button { width: 1fr; margin-top: 1; }
    #model-picker-custom { margin-top: 1; }
    """

    def __init__(self, provider: str, current_model: str) -> None:
        super().__init__()
        self.provider = provider
        self.current_model = current_model
        self._options = EXECUTOR_MODELS[provider]

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker"):
            yield Static(f"Choose {self.provider.title()} model", id="model-picker-title")
            yield Static(
                "Availability depends on the account signed in to the provider CLI.",
                id="model-picker-note",
            )
            yield Select(
                self._options,
                value=self._initial_choice(),
                allow_blank=False,
                id="model-picker-select",
            )
            yield Input(value=self._initial_custom(), placeholder="Custom model name", id="model-picker-custom")
            yield Button("Refresh all available Codex models", id="model-picker-refresh")
            yield Button("Use selected model", id="model-picker-save")
            yield Button("Cancel", id="model-cancel")

    def on_mount(self) -> None:
        self.query_one("#model-picker-refresh", Button).display = self.provider == "codex"
        self._show_custom_input()

    def _initial_choice(self) -> str:
        values = {value for _, value in self._options}
        return self.current_model if self.current_model in values else "custom"

    def _initial_custom(self) -> str:
        return "" if self._initial_choice() != "custom" else self.current_model

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "model-picker-select":
            self._show_custom_input()

    def _show_custom_input(self) -> None:
        choice = str(self.query_one("#model-picker-select", Select).value)
        self.query_one("#model-picker-custom", Input).display = choice == "custom"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "model-picker-save":
            choice = str(self.query_one("#model-picker-select", Select).value)
            custom = self.query_one("#model-picker-custom", Input).value.strip()
            if choice != "custom":
                self.dismiss(choice)
            elif custom:
                self.dismiss(custom)
            else:
                self.notify("Enter a custom model name first.", severity="warning")
        elif button_id == "model-picker-refresh":
            self._load_codex_model_catalog()
        elif button_id == "model-cancel":
            self.dismiss(None)

    @work(exclusive=True)
    async def _load_codex_model_catalog(self) -> None:
        button = self.query_one("#model-picker-refresh", Button)
        button.disabled = True
        button.label = "Loading Codex model catalog…"
        command = command_for_platform(
            ["codex", "debug", "models"], "win32" if os.name == "nt" else "posix"
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            if process.returncode != 0:
                raise RuntimeError(stderr.decode("utf-8", errors="replace").strip() or "Codex returned an error")
            payload = json.loads(stdout.decode("utf-8"))
            models = payload.get("models", [])
            options = [
                (str(model.get("display_name") or model["slug"]), str(model["slug"]))
                for model in models
                if model.get("slug") and model.get("visibility", "list") == "list"
            ]
            if not options:
                raise RuntimeError("The installed Codex CLI returned no selectable models.")
            options.sort(key=lambda option: option[0].lower())
            self._options = tuple(
                [("Use Codex recommended default", "default"), *options, ("Custom model…", "custom")]
            )
            selector = self.query_one("#model-picker-select", Select)
            selector.set_options(self._options)
            selector.value = self._initial_choice()
            self._show_custom_input()
            self.notify(f"Loaded {len(options)} Codex models from the installed CLI.")
        except (FileNotFoundError, OSError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            self.notify(f"Could not load Codex models: {error}", severity="warning")
        finally:
            button.disabled = False
            button.label = "Refresh all available Codex models"


class DenniceApp(App[None]):
    """Thin Textual control surface over Harness.run_events()."""

    TITLE = "Dennice"
    BINDINGS = [
        Binding("ctrl+n", "new_session", "New session", priority=True),
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
        height: 5;
        padding: 0 1;
        background: #202020;
        color: #e7e7e7;
        border-left: heavy #4d8dff;
        border-top: none;
        border-right: none;
        border-bottom: none;
    }
    #home-task.terminal-mode {
        border-left: heavy #ffd166;
        border-top: solid #ffd166;
        border-right: solid #ffd166;
        border-bottom: solid #ffd166;
    }
    #workspace-task.terminal-mode { border: tall #ffd166; }
    .terminal-mode-label {
        display: none;
        color: #d8aa45;
        margin: 0 1;
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
    #session-tabs {
        height: 2;
        padding: 0 2;
        background: #111111;
        border-bottom: solid #353535;
        align: left middle;
    }
    #session-tabs Button {
        width: auto;
        height: 1;
        min-width: 0;
        max-width: 22;
        border: none;
        background: #111111;
        color: #b8b8b8;
    }
    #session-tabs .session-tab {
        padding: 0 1;
        margin-right: 0;
    }
    #session-tabs .session-tab.-primary {
        background: #253656;
        color: #f2f6ff;
    }
    #session-tabs .session-close {
        width: 3;
        padding: 0;
        margin-right: 1;
        color: #888888;
    }
    #session-tabs .session-close:hover {
        background: #4b1e2f;
        color: #fff3fa;
    }
    #workspace-main { height: 1fr; }
    #details { display: none; width: 38; height: 1fr; }
    #workspace-task { height: 5; margin: 1 2; border: tall $accent; }
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
        self._sessions: list[ChatSession] = []
        self._active_session_index: int | None = None
        self._command_matches: list[tuple[str, str]] = []
        self._command_selection = 0
        self._history_cursor: int | None = None
        self._history_draft = ""

    @property
    def _conversation(self) -> list[ChatMessage]:
        """Compatibility view of the active session transcript."""
        return self._ensure_active_session().messages

    @_conversation.setter
    def _conversation(self, messages: list[ChatMessage]) -> None:
        self._ensure_active_session().messages = messages

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield self._mascot_widget("header-mascot", width=12, height=5)
            yield Static(self._masthead_text(), id="masthead-copy")
        with Vertical(id="home"):
            with Vertical(id="home-card"):
                with Horizontal(id="home-brand"):
                    yield self._mascot_widget("home-mascot", width=12, height=5)
                    yield Static(_wordmark_renderable(), id="home-title")
                yield TaskComposer(
                    placeholder='Ask anything…  Ctrl+Enter runs · ! pwd runs a terminal command',
                    id="home-task",
                )
                yield Static("", id="home-terminal-mode", classes="terminal-mode-label")
                yield Static("", id="home-command-menu", classes="command-menu")
                yield Static(
                    "Run  ·  Route  ·  Benchmark  ·  Setup",
                    id="home-modes",
                )
                yield Static(
                    "ctrl+enter run   enter newline   ! command terminal   /help commands   ctrl+n new session",
                    id="home-help",
                )
        with Vertical(id="workspace"):
            with Horizontal(id="session-tabs"):
                for index in range(8):
                    yield Button("", id=f"session-tab-{index}", classes="session-tab", compact=True)
                    yield Button("×", id=f"session-close-{index}", classes="session-close", compact=True)
            with Horizontal(id="workspace-main"):
                with Vertical(id="details"):
                    with Vertical(classes="detail-panel"):
                        yield Static("Cognitive routing\nAwaiting task.", id="routing")
                    with Vertical(classes="detail-panel"):
                        yield Static("Tools / events\nAwaiting task.", id="events")
                with Vertical(id="agent"):
                    yield Static("", id="output")
            yield TaskComposer(
                placeholder="Describe the next task…  Ctrl+Enter runs · ! command opens terminal mode",
                id="workspace-task",
            )
            yield Static("", id="workspace-terminal-mode", classes="terminal-mode-label")
            yield Static("", id="workspace-command-menu", classes="command-menu")
        yield Static(self._status_text(), id="statusline")
        yield Footer(id="footer")

    def on_mount(self) -> None:
        self.query_one("#home-task", TaskComposer).focus()
        self.set_interval(0.12, self._animate_activity)
        self._render_session_tabs()

    def submit_composer(self, composer: TaskComposer) -> None:
        """Submit a multiline composer explicitly; Enter itself remains a newline."""
        value = composer.value.strip()
        if not value:
            return
        session = self._ensure_active_session()
        if not session.input_history or session.input_history[-1] != value:
            session.input_history.append(value)
        self._history_cursor = None
        self._history_draft = ""
        if value.startswith("/"):
            self._run_slash_command(value)
        elif value.startswith("!"):
            self._start_terminal_command(value[1:].strip())
        else:
            self._start_run(value)
        composer.value = ""

    def recall_composer_history(self, composer: TaskComposer, direction: int) -> bool:
        """Recall submitted task, slash, or terminal commands at composer edges."""
        history = self._ensure_active_session().input_history
        if not history:
            return False
        if direction < 0:
            if self._history_cursor is None:
                self._history_draft = composer.value
                self._history_cursor = len(history) - 1
            else:
                self._history_cursor = max(0, self._history_cursor - 1)
            composer.value = history[self._history_cursor]
            composer.action_cursor_line_end()
            return True
        if self._history_cursor is None:
            return False
        if self._history_cursor < len(history) - 1:
            self._history_cursor += 1
            composer.value = history[self._history_cursor]
        else:
            composer.value = self._history_draft
            self._history_cursor = None
        composer.action_cursor_line_end()
        return True

    def on_input_changed(self, event: Input.Changed) -> None:
        menu_id = "#home-command-menu" if event.input.id == "home-task" else "#workspace-command-menu"
        self._show_command_menu(menu_id, event.value)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        menu_id = "#home-command-menu" if event.text_area.id == "home-task" else "#workspace-command-menu"
        self._show_command_menu(menu_id, event.text_area.text)
        self._update_terminal_mode(event.text_area)

    def _update_terminal_mode(self, composer: TextArea) -> None:
        is_terminal = composer.text.lstrip().startswith("!")
        composer.set_class(is_terminal, "terminal-mode")
        label_id = "#home-terminal-mode" if composer.id == "home-task" else "#workspace-terminal-mode"
        label = self.query_one(label_id, Static)
        label.display = is_terminal
        if is_terminal:
            label.update(Text("!  ", style="bold #ffd166") + Text("Terminal mode · Ctrl+Enter executes in this project", style="#b7a36e"))

    def action_run_task(self) -> None:
        self._start_run(self._active_task_input().value)

    def action_new_session(self) -> None:
        session_number = len(self._sessions) + 1
        self._sessions.append(
            ChatSession(
                id=f"session-{session_number}",
                title=f"Session {session_number}",
                messages=[],
                input_history=[],
            )
        )
        self._active_session_index = len(self._sessions) - 1
        self._history_cursor = None
        self._history_draft = ""
        self._run_is_active = False
        self._activate_workspace("")
        self._render_session_tabs()
        task = self.query_one("#workspace-task", TaskComposer)
        task.value = ""
        task.focus()

    def action_new_task(self) -> None:
        """Compatibility action for the original keybinding and slash command."""
        self.action_new_session()

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
        self.notify(
            f"Saved {selection.executor.provider}/{selection.executor.model}; "
            f"router: {selection.router.provider}"
        )

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
            self.action_new_session()
        elif command == "session":
            self._select_session_from_command(argument)
        elif command == "sessions":
            self._list_sessions()
        elif command == "rename":
            self._rename_current_session(argument)
        elif command == "setup":
            self.action_setup()
        elif command == "config":
            self._show_session_config()
        elif command == "model":
            if argument:
                self._set_executor_model(argument)
            else:
                self._open_model_picker()
        elif command == "effort":
            self._set_executor_effort(argument)
        elif command in {"permission", "permissions"}:
            self._set_permission_mode(argument)
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

    def _select_session_from_command(self, value: str) -> None:
        try:
            session_number = int(value)
        except ValueError:
            self._show_local_message("Usage: /session <number>")
            return
        index = session_number - 1
        if not 0 <= index < len(self._sessions):
            self._show_local_message(f"No open session {session_number}.")
            return
        self._select_session(index)

    def _list_sessions(self) -> None:
        if not self._sessions:
            self._show_local_message("No sessions are open. Use /new to start one.")
            return
        sessions = []
        for index, session in enumerate(self._sessions, start=1):
            active = " (current)" if index - 1 == self._active_session_index else ""
            sessions.append(f"{index}. {session.title}{active}")
        self._show_local_message("Open sessions\n\n" + "\n".join(sessions) + "\n\nUse /session <number> to switch.")

    def _rename_current_session(self, title: str) -> None:
        normalized = " ".join(title.split())
        if not normalized:
            self._show_local_message("Usage: /rename <title>")
            return
        session = self._ensure_active_session()
        session.title = normalized[:24] + ("…" if len(normalized) > 24 else "")
        self._render_session_tabs()
        self.notify(f"Renamed session to {session.title}")

    def _show_session_config(self) -> None:
        executor = self.harness.config.executor
        effort = executor.reasoning_effort.value if executor.reasoning_effort else "default"
        permission = self._effective_permission_mode().value
        self._show_local_message(
            "Session configuration\n\n"
            f"Executor: {executor.provider}\n"
            f"Model: {executor.model}\n"
            f"Effort: {effort}\n"
            f"Permissions: {permission}\n"
            f"Router: {self.harness.config.router.provider}"
        )

    def _set_executor_model(self, value: str) -> None:
        if not value:
            self._show_local_message("Usage: /model <name>")
            return
        config = self.harness.config.model_copy(deep=True)
        if config.executor.provider == "mock":
            self._show_local_message("Select Codex or Claude in /setup before setting a model.")
            return
        config.executor.model = value
        self._save_config(config, f"Model set to {value}")

    def _open_model_picker(self) -> None:
        executor = self.harness.config.executor
        if executor.provider not in EXECUTOR_MODELS:
            self._show_local_message("Select Codex or Claude in /setup before choosing a model.")
            return
        self.push_screen(
            ModelPickerScreen(executor.provider, executor.model),
            self._apply_model_picker,
        )

    def _apply_model_picker(self, model: str | None) -> None:
        if model is not None:
            self._set_executor_model(model)

    def _set_executor_effort(self, value: str) -> None:
        normalized = value.lower() or "default"
        if normalized == "default":
            effort = None
        else:
            try:
                effort = ReasoningEffort(normalized)
            except ValueError:
                self._show_local_message("Usage: /effort <low|medium|high|xhigh|default>")
                return
        config = self.harness.config.model_copy(deep=True)
        if config.executor.provider == "mock":
            self._show_local_message("Select Codex or Claude in /setup before setting effort.")
            return
        config.executor.reasoning_effort = effort
        self._save_config(config, f"Effort set to {normalized}")

    def _set_permission_mode(self, value: str) -> None:
        normalized = value.lower()
        try:
            permission = PermissionMode(normalized)
        except ValueError:
            self._show_local_message("Usage: /permissions <read-only|workspace-write|plan>")
            return
        config = self.harness.config.model_copy(deep=True)
        if config.executor.provider == "mock":
            self._show_local_message("Select Codex or Claude in /setup before setting permissions.")
            return
        if config.executor.provider == "claude" and permission != PermissionMode.PLAN:
            self._show_local_message("Claude Code is currently run in safe plan mode. Use /permissions plan.")
            return
        if config.executor.provider == "codex" and permission == PermissionMode.PLAN:
            self._show_local_message("Codex supports /permissions read-only or /permissions workspace-write.")
            return
        config.executor.permission_mode = permission
        self._save_config(config, f"Permissions set to {permission.value}")

    def _save_config(self, config: DenniceConfig, message: str) -> None:
        config.save()
        self.harness = Harness(config)
        self.query_one("#masthead-copy", Static).update(self._masthead_text())
        self.notify(message)

    def _effective_permission_mode(self) -> PermissionMode:
        configured = self.harness.config.executor.permission_mode
        if configured is not None:
            return configured
        return (
            PermissionMode.PLAN
            if self.harness.config.executor.provider == "claude"
            else PermissionMode.READ_ONLY
        )

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
        self._ensure_active_session().messages.append(ChatMessage("assistant", message))
        self._show_transcript()

    def _start_run(self, task: str) -> None:
        if task.strip():
            config = self.harness.config
            if config.router.provider == "jev" and not os.environ.get(config.jev.api_key_env):
                self._show_local_message(
                    "Hosted Jev cannot route this task because its API key is unavailable.\n\n"
                    f"Export {config.jev.api_key_env} in this terminal, or open /setup and choose "
                    "Rule, Codex, or OpenJev · local as the cognitive router."
                )
                return
            session = self._ensure_active_session()
            history = [
                {"role": message.role, "content": message.content}
                for message in session.messages
                if message.content.strip() and message.role in {"user", "assistant"}
            ]
            request = Task(
                prompt=task,
                context={"conversation_history": history} if history else {},
            )
            assistant_message = ChatMessage("assistant", "")
            session.messages.extend((ChatMessage("user", task), assistant_message))
            if session.title.startswith("Session "):
                session.title = self._session_title(task)
            self._activate_workspace(task)
            self._render_session_tabs()
            self._run(request, assistant_message)

    def _start_terminal_command(self, command: str) -> None:
        """Run an explicit user terminal request in the project directory.

        Unlike an agent tool call, a command prefixed by ``!`` is direct user
        input. It is still kept out of model conversation history and clearly
        labelled in the transcript.
        """
        if not command:
            self._show_local_message("Usage: ! <bash command>\n\nExample: ! git status --short")
            return
        session = self._ensure_active_session()
        terminal_message = ChatMessage("terminal", "Running terminal command…")
        session.messages.extend((ChatMessage("user", f"! {command}"), terminal_message))
        if session.title.startswith("Session "):
            session.title = self._session_title(command)
        self._activate_workspace("")
        self._render_session_tabs()
        self._run_terminal(command, terminal_message)

    @work(exclusive=True)
    async def _run_terminal(self, command: str, terminal_message: ChatMessage) -> None:
        """Capture a bounded Bash command result without making it an agent tool."""
        try:
            process = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                command,
                cwd=str(Path.cwd()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            except TimeoutError:
                process.kill()
                await process.wait()
                terminal_message.content = (
                    f"$ {command}\n\nCommand timed out after 30 seconds and was stopped."
                )
            else:
                output = stdout.decode("utf-8", errors="replace")
                error = stderr.decode("utf-8", errors="replace")
                rendered = (output + (f"\n[stderr]\n{error}" if error else "")).strip()
                if len(rendered) > 24_000:
                    rendered = rendered[:24_000] + "\n\n[output truncated at 24 KB]"
                if not rendered:
                    rendered = "[no output]"
                status = "completed" if process.returncode == 0 else f"failed (exit {process.returncode})"
                terminal_message.content = f"$ {command}\n\n{rendered}\n\n[{status}]"
        except FileNotFoundError:
            terminal_message.content = (
                "Terminal mode needs Bash on PATH. On Windows, install Git Bash or WSL and restart Dennice."
            )
        except OSError as error:
            terminal_message.content = f"Terminal command could not start: {error}"
        self._show_transcript()

    def _active_task_input(self) -> TaskComposer:
        if self.query_one("#workspace", Vertical).display:
            return self.query_one("#workspace-task", TaskComposer)
        return self.query_one("#home-task", TaskComposer)

    def _activate_workspace(self, task: str) -> None:
        self.query_one("#home", Vertical).display = False
        self.query_one("#workspace", Vertical).display = True
        self.query_one("#masthead", Horizontal).display = True
        self.query_one("#footer", Footer).display = True
        workspace_task = self.query_one("#workspace-task", TaskComposer)
        workspace_task.value = task
        workspace_task.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("session-tab-"):
            index = int(event.button.id.removeprefix("session-tab-"))
            if index < len(self._sessions):
                self._select_session(index)
        elif event.button.id and event.button.id.startswith("session-close-"):
            index = int(event.button.id.removeprefix("session-close-"))
            if index < len(self._sessions):
                self._close_session(index)

    def _ensure_active_session(self) -> ChatSession:
        if self._active_session_index is None:
            self._sessions.append(
                ChatSession(id="session-1", title="Session 1", messages=[], input_history=[])
            )
            self._active_session_index = 0
            self._render_session_tabs()
        return self._sessions[self._active_session_index]

    def _select_session(self, index: int) -> None:
        self._active_session_index = index
        self._history_cursor = None
        self._history_draft = ""
        self._run_is_active = False
        self._activate_workspace("")
        self._render_session_tabs()
        self._show_transcript()

    def _close_session(self, index: int) -> None:
        """Close one in-memory session and select its nearest surviving neighbour."""
        closing = self._sessions.pop(index)
        if not self._sessions:
            self._active_session_index = None
            self._activate_workspace("")
            self._ensure_active_session()
        elif self._active_session_index is None or self._active_session_index == index:
            self._active_session_index = min(index, len(self._sessions) - 1)
        elif self._active_session_index > index:
            self._active_session_index -= 1
        self._render_session_tabs()
        self._show_transcript()
        self.notify(f"Closed {closing.title}")

    def _render_session_tabs(self) -> None:
        for index in range(8):
            button = self.query_one(f"#session-tab-{index}", Button)
            close = self.query_one(f"#session-close-{index}", Button)
            if index >= len(self._sessions):
                button.display = False
                close.display = False
                continue
            session = self._sessions[index]
            active = index == self._active_session_index
            button.display = True
            close.display = True
            button.label = f"{index + 1}  {session.title}"
            button.variant = "primary" if active else "default"

    @staticmethod
    def _session_title(task: str) -> str:
        normalized = " ".join(task.split())
        return normalized[:24] + ("…" if len(normalized) > 24 else "")

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
        for index, message in enumerate(self._ensure_active_session().messages):
            label_style = (
                "bold #81aaff"
                if message.role == "user"
                else "bold #ffd166"
                if message.role == "terminal"
                else "bold #ff83c1"
            )
            label = "You" if message.role == "user" else "Terminal" if message.role == "terminal" else "Dennice"
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
        executor = self.harness.config.executor
        effort = executor.reasoning_effort.value if executor.reasoning_effort else "default"
        return (
            "Dennice\n"
            "Cognitive Data Agent Harness\n"
            f"executor: {self.harness.executor.id}-{self.harness.executor.version}\n"
            f"model: {executor.model}\n"
            f"effort: {effort}\n"
            f"permissions: {self._effective_permission_mode().value}\n"
            f"router: {self.harness.config.router.provider}"
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
