from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
import shlex
from pathlib import Path

from PIL import Image as PILImage
from rich.style import Style
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, OptionList, Select, Static, TextArea
import regex
from time import monotonic
from textual.widgets.option_list import Option

from dennice import __version__
from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.runner import BenchmarkRunner
from dennice.core.config import DenniceConfig, HookConfig, MCPServerConfig, PermissionMode, ProviderConfig, ReasoningEffort
from dennice.tui.extensions import ExtensionConfigScreen
from dennice.core.attachments import paste_image, validate_image
from dennice.core.harness import Harness
from dennice.core.goals import GoalController, GoalState
from dennice.core.hooks import fingerprint
from dennice.core.config import ModelCandidate
from dennice.core.model_catalog import load_claude_model_catalog
from dennice.core.jsonrpc import load_codex_model_catalog
from dennice.core.models import BenchmarkMode, EventKind, Task
from dennice.core.process import command_for_platform, process_group_options, read_bounded, stop_process
from dennice.core.skills import discover_skills
from dennice.executors.api import API_DEFAULTS, API_PROVIDERS, load_api_models
from dennice.core.verification import verify_executor, verify_router
from dennice.core.accounting import summarize_usage
from dennice.runs.sessions import ChatMessage, ChatSession, SessionStore
from dennice.tui.transcript import Transcript
from dennice.tui.files import DirectoryPicker, FileInput, FilePreviewPane, GitDiffPane, WorkspaceFiles


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
    "copilot": (
        ("Use GitHub Copilot default", "default"),
        ("Claude Sonnet 4.6", "claude-sonnet-4.6"),
        ("GPT-5.4", "gpt-5.4"),
        ("GPT-6 Astra", "gpt-6-astra"),
        ("GPT-6 Sol", "gpt-6-sol"),
        ("GPT-6 Luna", "gpt-6-luna"),
        ("Claude Opus 5.5", "claude-opus-5.5"),
        ("Claude Haiku 4.5", "claude-haiku-4.5"),
        ("GPT-5.3 Codex", "gpt-5.3-codex"),
        ("Gemini 3.7 Flash", "gemini-3.7-flash"),
        ("Custom model…", "custom"),
    ),
}
for _api_provider in API_PROVIDERS:
    EXECUTOR_MODELS[_api_provider] = (("Enter model ID…", "custom"),)
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")
SLASH_COMMANDS = (
    ("/new", "start a fresh conversation"),
    ("/setup", "choose provider, model, effort, and permissions"),
    ("/config", "show executor, model, effort, and permissions"),
    ("/model", "choose a model for the configured executor"),
    ("/compact", "summarize older local history and preserve the visible transcript"),
    ("/skills", "browse local Claude and Codex skills"),
    ("/attach <path>", "attach an image file as context"),
    ("/clear-images", "remove pending image attachments"),
    ("/skill <name> <task>", "explicitly run a local skill for a task"),
    ("/effort <level>", "set low, medium, high, xhigh, or default"),
    ("/permissions <mode>", "set read-only, read-write, or plan"),
    ("/session <number>", "switch to an open session tab"),
    ("/sessions", "list open sessions"),
    ("/resume <id>", "reopen an archived session"),
    ("/rename <title>", "rename the current session"),
    ("/details", "show routing and event details"),
    ("/route <task>", "classify without execution"),
    ("/run <task>", "route and execute a task"),
    ("/benchmark", "run the configured benchmark"),
    ("/help", "show all commands"),
    ("/routing", "fixed, shadow, auto; pin/unpin model or effort"),
    ("/pool", "list/add/remove explicitly approved routing models"),
    ("/tools", "enable or disable native API tools"),
    ("/hooks", "list, trust, enable or disable configured hooks"),
    ("/mcp", "list, trust, enable, disable or test configured servers"),
    ("/goal", "create, status, pause, resume or cancel a bounded goal"),
    ("/recovery", "inspect interrupted runs and explicitly reconcile uncertain effects"),
    ("/cwd [path]", "show or explicitly change this session's working directory"),
)


@dataclass
class SetupSelection:
    executor: ProviderConfig
    router: ProviderConfig
    permission_mode: PermissionMode | None
    jev_api_key_env: str
    openjev_endpoint: str
    openjev_model: str
    hooks: list[HookConfig] = field(default_factory=list)
    mcp: list[MCPServerConfig] = field(default_factory=list)

class TaskComposer(TextArea):
    """Multiline task composer; Ctrl+Enter submits and Enter inserts a newline."""

    BINDINGS = [Binding("ctrl+v", "paste_image_or_text", "Paste", priority=True)]

    def action_paste_image_or_text(self) -> None:
        self.app.paste_clipboard_image(self)

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

    BINDINGS = [Binding("escape", "cancel_setup", "Cancel", priority=True)]

    def action_cancel_setup(self) -> None:
        self.dismiss(None)

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
    #setup-dialog Input { height: 1; min-height: 1; border: none; padding: 0 1; background: #292929; color: #e6e6e6; }
    #setup-dialog Select { height: 1; min-height: 1; border: none; padding: 0 1; background: #292929; color: #e6e6e6; }
    #setup-buttons, #setup-api-buttons, #setup-router-buttons { height: 1; margin-top: 1; }
    #setup-buttons Button, #setup-api-buttons Button, #setup-router-buttons Button { width: 1fr; min-width: 0; margin-right: 1; }
    #setup-model-label, #setup-effort-label, #setup-claude-auth-label, #setup-router-model-label, #setup-openjev-label, #setup-jev-label { color: #d8d8d8; margin-top: 1; }
    #setup-effort-buttons { height: 1; }
    #setup-effort-buttons Button { width: 1fr; min-width: 0; margin-right: 1; padding: 0; }
    #setup-permission-label { color: #d8d8d8; margin-top: 1; }
    #setup-permission-buttons { height: 1; }
    #setup-permission-buttons Button { margin-right: 1; }
    #setup-dialog Button { height: 1; min-height: 1; padding: 0 1; background: #2d2d2d; color: #e6e6e6; }
    #setup-dialog Button:hover { background: #414141; }
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
    #setup-copilot-note { color: #d8c08a; margin: 1 0; }
    #setup-extensions { height: 1; margin-top: 2; }
    #setup-extensions Button { background: #375679; color: white; margin-right: 2; }
    """

    def __init__(self, config: DenniceConfig) -> None:
        super().__init__()
        self.executor_provider = (
            config.executor.provider if config.executor.provider in {"mock", "codex", "claude", "copilot", *API_PROVIDERS} else "mock"
        )
        self.executor_model = config.executor.model
        self._codex_model_options = EXECUTOR_MODELS["codex"]
        self._claude_model_options = EXECUTOR_MODELS["claude"]
        self._api_model_options = {provider: EXECUTOR_MODELS[provider] for provider in API_PROVIDERS}
        self.api_base_url = config.executor.base_url or API_DEFAULTS.get(self.executor_provider, ("", ""))[0]
        self.api_key_env = config.executor.api_key_env
        self.claude_cli_auth = config.executor.claude_cli_auth or "subscription"
        self.codex_cli_auth = config.executor.codex_cli_auth or "chatgpt"
        if self.api_key_env is None:
            self.api_key_env = API_DEFAULTS.get(self.executor_provider, ("", ""))[1]
        self.reasoning_effort = (
            config.executor.reasoning_effort.value if config.executor.reasoning_effort else "default"
        )
        self.permission_mode = config.executor.permission_mode
        self.router_provider = (
            config.router.provider if config.router.provider in {"rule", "codex", "jev", "openjev"} else "rule"
        )
        self.router_model = config.router.model
        self.router_codex_cli_auth = config.router.codex_cli_auth or "chatgpt"
        self.jev_api_key_env = config.jev.api_key_env
        self.openjev_endpoint = config.openjev.endpoint
        self.openjev_model = config.openjev.model
        self._connection_tests: dict[str, str] = {}
        self._connection_frame = 0
        self.hook_configs = [item.model_copy(deep=True) for item in config.hooks]
        self.mcp_configs = [item.model_copy(deep=True) for item in config.mcp]

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield Static("Dennice Setup", id="setup-title")
            yield Static(
                "Choose two independent providers: the executor answers your task; the router selects its reasoning policies. Changing one does not change the other.",
                id="setup-description",
            )
            yield Static("System 2 executor", id="setup-provider")
            with Horizontal(id="setup-buttons"):
                yield Button("Codex", id="setup-codex", compact=True)
                yield Button("Claude", id="setup-claude", compact=True)
            with Horizontal(id="setup-api-buttons"):
                yield Button("OpenAI API", id="setup-openai-api", compact=True)
                yield Button("Anthropic API", id="setup-anthropic-api", compact=True)
                yield Button("Local", id="setup-local", compact=True)
                yield Button("GitHub Copilot", id="setup-copilot", compact=True)
            yield Static("API/local base URL", id="setup-api-url-label")
            yield Input(value=self.api_base_url, id="setup-api-url")
            yield Static("API key environment variable (name only; optional for local)", id="setup-api-key-label")
            yield Input(value=self.api_key_env, id="setup-api-key")
            yield Static("API/local tools are disabled by default; enable with /tools on. Permission controls govern native tools, not an OS sandbox. Shell commands and MCP require explicit approval. Selected skill manifests can be provided as user context. API calls may incur usage charges.", id="setup-api-note")
            yield Static(
                "Copilot CLI uses its stored account (or the `gh` fallback). Nonempty COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN stops startup. GitHub may use individual-plan prompts and outputs for model improvement unless opted out in account settings; Dennice's local telemetry setting does not change that policy.",
                id="setup-copilot-note",
            )
            yield Static("Claude Code CLI authentication (checked before each run)", id="setup-claude-auth-label")
            yield Select((("Subscription", "subscription"), ("API key", "api_key"),
                          ("Provider default / other", "provider_default")),
                         value=self.claude_cli_auth, allow_blank=False, id="setup-claude-auth")
            yield Static("Codex CLI authentication (checked before each run)", id="setup-codex-auth-label")
            yield Select((("ChatGPT plan", "chatgpt"), ("API key", "api_key"),
                          ("Provider default / other", "provider_default")),
                         value=self.codex_cli_auth, allow_blank=False, id="setup-codex-auth")
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
                yield Button("Read-write", id="permission-workspace-write", compact=True)
                yield Button("Plan", id="permission-plan", compact=True)
            yield Button("Test executor (live)", id="setup-test-executor", compact=True)
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
            yield Static("Codex router authentication (checked before classification)", id="setup-router-codex-auth-label")
            yield Select((("ChatGPT plan", "chatgpt"), ("API key", "api_key"),
                          ("Provider default / other", "provider_default")),
                         value=self.router_codex_cli_auth, allow_blank=False, id="setup-router-codex-auth")
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
            with Horizontal(id="setup-extensions"):
                yield Button("Configure hooks", id="setup-hooks", compact=True)
                yield Button("Configure MCP", id="setup-mcp", compact=True)
            yield Button("Save configuration", variant="primary", id="setup-save", compact=True)
            yield Button("Cancel", id="setup-cancel", compact=True)

    def on_mount(self) -> None:
        self._show_selection()
        self.set_interval(0.12, self._animate_connection_tests)
        if self.executor_provider == "codex":
            self._load_codex_model_catalog()
        elif self.executor_provider == "claude":
            self._load_claude_model_catalog()
        elif self.executor_provider == "copilot":
            pass  # Catalog entries are examples from the current CLI docs; custom IDs remain supported.
        elif self.executor_provider in API_PROVIDERS:
            self._load_api_model_catalog()

    def on_key(self, event: events.Key) -> None:
        """Navigate the form without stealing dropdown or text editing keys."""
        if event.key not in {"up", "down", "left", "right"}:
            return
        if any(selector.expanded for selector in self.query(Select)):
            return
        focused = self.focused
        target = None
        if event.key in {"left", "right"}:
            if not isinstance(focused, Button) or not isinstance(focused.parent, Horizontal):
                return
            buttons = [
                button for button in focused.parent.query(Button)
                if button.display and not button.disabled
            ]
            if focused not in buttons:
                return
            step = 1 if event.key == "right" else -1
            target = buttons[(buttons.index(focused) + step) % len(buttons)]
            target.focus()
        elif event.key == "down":
            target = self.focus_next()
        else:
            target = self.focus_previous()
        if target is not None:
            target.scroll_visible(animate=False)
        event.prevent_default()
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in {f"setup-{provider}" for provider in API_PROVIDERS}:
            self.executor_provider = event.button.id.removeprefix("setup-")
            base, env = API_DEFAULTS[self.executor_provider]
            self.query_one("#setup-api-url", Input).value = base
            self.query_one("#setup-api-key", Input).value = env
            self.executor_model = ""
            self.reasoning_effort = "default"
            self.permission_mode = PermissionMode.READ_ONLY
            self._refresh_model_choices()
            self._show_selection()
            self._load_api_model_catalog()
        elif event.button.id == "setup-codex":
            self.executor_provider = "codex"
            if self.permission_mode not in {
                PermissionMode.READ_ONLY,
                PermissionMode.WORKSPACE_WRITE,
                PermissionMode.PLAN,
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
            self._load_claude_model_catalog()
        elif event.button.id == "setup-copilot":
            self.executor_provider = "copilot"
            self.permission_mode = PermissionMode.READ_ONLY
            self.executor_model = "default"
            self.reasoning_effort = "default"
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
            if self.executor_provider in API_PROVIDERS:
                self._load_api_model_catalog()
            elif self.executor_provider == "claude":
                self._load_claude_model_catalog()
            else:
                self._load_codex_model_catalog()
        elif event.button.id == "setup-test-executor":
            self._test_connection("executor")
        elif event.button.id == "setup-test-router":
            self._test_connection("router")
        elif event.button.id in {"setup-hooks", "setup-mcp"}:
            kind = event.button.id.removeprefix("setup-")
            entries = self.hook_configs if kind == "hooks" else self.mcp_configs
            def applied(result):
                if result is not None:
                    if kind == "hooks":
                        self.hook_configs = result
                    else:
                        self.mcp_configs = result
            self.app.push_screen(ExtensionConfigScreen(kind, entries), applied)
        elif event.button.id == "setup-save":
            self.dismiss(self._selection_from_fields())
        elif event.button.id == "setup-cancel":
            self.dismiss(None)

    def _show_selection(self) -> None:
        executor_labels = {
            "mock": "Choose an executor provider below",
            "codex": "Codex CLI (checks selected CLI authentication before each run)",
            "claude": "Claude Code (checks selected CLI authentication before each run)",
            "copilot": "GitHub Copilot CLI (uses the current GitHub Copilot subscription/login)",
            "openai-api": "OpenAI API (environment key; separate API billing)",
            "anthropic-api": "Anthropic API (environment key; separate API billing)",
            "local": "Local OpenAI-compatible server (Ollama / LM Studio)",
        }
        label = executor_labels[self.executor_provider]
        self.query_one("#setup-provider", Static).update(f"System 2 executor: {label}")
        test_executor = self.query_one("#setup-test-executor", Button)
        test_executor.label = f"Test executor (live) · {self.executor_provider.title()}"
        test_executor.display = self.executor_provider != "mock"
        self.query_one("#setup-test-router", Button).label = (
            f"Test router{' (live)' if self.router_provider != 'rule' else ''} · {self.router_provider.title()}"
        )
        model_selected = self.executor_provider != "mock"
        for widget_id in ("#setup-api-url-label", "#setup-api-url", "#setup-api-key-label", "#setup-api-key", "#setup-api-note"):
            self.query_one(widget_id).display = self.executor_provider in API_PROVIDERS
        for widget_id in ("#setup-claude-auth-label", "#setup-claude-auth"):
            self.query_one(widget_id).display = self.executor_provider == "claude"
        for widget_id in ("#setup-codex-auth-label", "#setup-codex-auth"):
            self.query_one(widget_id).display = self.executor_provider == "codex"
        self.query_one("#setup-copilot-note", Static).display = self.executor_provider == "copilot"
        for widget_id in ("#setup-model-label", "#setup-model-choice"):
            self.query_one(widget_id).display = model_selected
        model_choice = str(self.query_one("#setup-model-choice", Select).value)
        self.query_one("#setup-model-custom", Input).display = model_selected and model_choice == "custom"
        refresh = self.query_one("#setup-refresh-codex-models", Button)
        refresh.display = model_selected and self.executor_provider != "copilot"
        if not refresh.disabled:
            refresh.label = f"Refresh available {self.executor_provider.title()} models"
        for widget_id in ("#setup-effort-label", "#setup-effort-buttons"):
            self.query_one(widget_id).display = model_selected and self.executor_provider != "local"
        for widget_id in ("#setup-permission-label", "#setup-permission-buttons"):
            self.query_one(widget_id).display = model_selected
        self.query_one("#setup-save", Button).disabled = self.executor_provider == "mock"
        effort_label = "Default" if self.reasoning_effort == "default" else self.reasoning_effort.upper()
        effort_note = (
            "native Codex setting"
            if self.executor_provider == "codex"
            else "native Claude Code CLI setting with prompt guidance"
        )
        if self.executor_provider == "copilot":
            effort_note = "native Copilot CLI setting"
        if self.executor_provider in API_PROVIDERS:
            effort_note = "API setting; selected model must support this level"
        self.query_one("#setup-effort-label", Static).update(
            f"Execution effort: {effort_label} ({effort_note})"
        )
        self._set_button_selection(
            {
                "setup-codex": self.executor_provider == "codex",
                "setup-claude": self.executor_provider == "claude",
                "setup-copilot": self.executor_provider == "copilot",
                **{f"setup-{provider}": self.executor_provider == provider for provider in API_PROVIDERS},
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
        for widget_id in ("#setup-router-codex-auth-label", "#setup-router-codex-auth"):
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
            "Plan (no file edits; planning instructions)"
            if self.permission_mode == PermissionMode.PLAN
            else ("Read-write (file edits allowed; no Bash or MCP)"
                  if self.executor_provider in {"claude", "copilot"} and self.permission_mode == PermissionMode.WORKSPACE_WRITE
                  else "Read-write" if self.permission_mode == PermissionMode.WORKSPACE_WRITE
                  else "Read only")
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
                self.executor_provider in {"codex", "claude", "copilot", *API_PROVIDERS}
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "setup-model-choice":
            return
        if event.value not in {value for _, value in self._model_options()}:
            return
        if str(event.value) != "custom":
            self.executor_model = str(event.value)
        self._show_selection()

    def _model_options(self) -> tuple[tuple[str, str], ...]:
        if self.executor_provider in API_PROVIDERS:
            return self._api_model_options[self.executor_provider]
        if self.executor_provider == "codex":
            return self._codex_model_options
        if self.executor_provider == "claude":
            return self._claude_model_options
        if self.executor_provider == "copilot":
            return EXECUTOR_MODELS["copilot"]
        return EXECUTOR_MODELS.get(self.executor_provider, (("Use default", "default"),))

    @work(exclusive=True)
    async def _load_api_model_catalog(self) -> None:
        provider = self.executor_provider
        config = ProviderConfig(provider=provider, model=self.executor_model,
                                base_url=self.query_one("#setup-api-url", Input).value.strip(),
                                api_key_env=self.query_one("#setup-api-key", Input).value.strip())
        button = self.query_one("#setup-refresh-codex-models", Button)
        button.disabled = True
        button.label = "Loading model catalog…"
        try:
            self._api_model_options[provider] = await load_api_models(config)
            if self.query("#setup-provider") and self.executor_provider == provider:
                self._refresh_model_choices()
        except (OSError, ValueError, RuntimeError) as error:
            self.notify(f"Could not load models: {error}", severity="warning")
        finally:
            button.disabled = False
            if self.query("#setup-provider"):
                self._show_selection()

    @work(exclusive=True)
    async def _load_claude_model_catalog(self) -> None:
        button = self.query_one("#setup-refresh-codex-models", Button)
        button.disabled = True
        button.label = "Loading Claude model catalog…"
        try:
            self._claude_model_options = await load_claude_model_catalog()
            if self.query("#setup-provider") and self.executor_provider == "claude":
                self._refresh_model_choices()
        except (OSError, TimeoutError, ValueError, RuntimeError) as error:
            self.notify(f"Could not load Claude models; showing aliases: {error}", severity="warning")
        finally:
            button.disabled = False
            if self.query("#setup-provider"):
                self._show_selection()

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
                base_url=self.query_one("#setup-api-url", Input).value.strip() if self.executor_provider in API_PROVIDERS else None,
                api_key_env=self.query_one("#setup-api-key", Input).value.strip() if self.executor_provider in API_PROVIDERS else None,
                claude_cli_auth=str(self.query_one("#setup-claude-auth", Select).value) if self.executor_provider == "claude" else None,
                codex_cli_auth=str(self.query_one("#setup-codex-auth", Select).value) if self.executor_provider == "codex" else None,
            ),
            router=ProviderConfig(provider=self.router_provider, model=router_model,
                                  codex_cli_auth=str(self.query_one("#setup-router-codex-auth", Select).value)
                                  if self.router_provider == "codex" else None),
            permission_mode=permission_mode,
            jev_api_key_env=env_name,
            openjev_endpoint=endpoint,
            openjev_model=openjev_model,
            hooks=[item.model_copy(deep=True) for item in self.hook_configs],
            mcp=[item.model_copy(deep=True) for item in self.mcp_configs],
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
        config.hooks = selection.hooks
        config.mcp = selection.mcp
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
            live = component == "executor" or (component == "router" and current_provider != "rule")
            button.label = f"Test {component}{' (live)' if live else ''} · {current_provider.title()}"

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
        options = self._model_options()
        choice = self._initial_model_choice()
        # Changing providers can retain the same 'default' value. Force a
        # public selection transition so Textual refreshes the visible label.
        with selector.prevent(Select.Changed):
            selector.set_options((("Refreshing…", "__refresh__"),))
            selector.set_options(options)
            selector.value = choice
        self.query_one("#setup-model-custom", Input).value = self._initial_custom_model()

    @work(exclusive=True)
    async def _load_codex_model_catalog(self) -> None:
        """Ask the installed, signed-in Codex CLI which models it can expose."""
        button = self.query_one("#setup-refresh-codex-models", Button)
        selector = self.query_one("#setup-model-choice", Select)
        button.disabled = True
        button.label = "Loading Codex model catalog…"
        selector.disabled = True
        try:
            self._codex_model_options = await load_codex_model_catalog()
            if self.executor_provider == "codex":
                self._refresh_model_choices()
                self.notify(f"Loaded {len(self._codex_model_options) - 2} Codex models from the installed CLI.")
        except (FileNotFoundError, OSError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            self.notify(f"Could not load Codex models: {error}", severity="warning")
        finally:
            selector.disabled = False
            button.disabled = False
            if self.query("#setup-provider"):
                self._show_selection()

    def _set_button_selection(self, selected: dict[str, bool]) -> None:
        """Give the selected provider an explicit marker, independent of keyboard focus."""
        labels = {
            "setup-openai-api": "OpenAI API",
            "setup-anthropic-api": "Anthropic API",
            "setup-local": "Local",
            "setup-codex": "Codex",
            "setup-claude": "Claude",
            "setup-copilot": "GitHub Copilot",
            "router-rule": "Rule",
            "router-codex": "Codex",
            "router-jev": "Jev",
            "router-openjev": "OpenJev",
            "permission-read-only": "Read only",
            "permission-workspace-write": "Read-write",
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


class SessionPickerScreen(ModalScreen[str | None]):
    """Keyboard-selectable open and archived conversations."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel", priority=True)]
    CSS = """
    SessionPickerScreen { align: center middle; background: #000000aa; }
    #session-picker { width: 70; max-width: 95%; height: auto; max-height: 85%;
        padding: 1 2; border: tall #f03c95; background: #161616; }
    #session-picker-options { height: auto; max-height: 18; margin-top: 1; }
    #session-picker-search { margin-top: 1; }
    #session-picker-status { color: #aaa; height: auto; }
    #session-picker-note { color: #aaa; margin-top: 1; }
    """

    def __init__(self, sessions: list[ChatSession], active_id: str | None) -> None:
        super().__init__()
        self.sessions = sessions
        self.active_id = active_id
        self._search_text = {
            session.id: session.title + "\n" + "\n".join(message.content for message in session.messages)
            for session in sessions
        }

    def _option(self, session: ChatSession) -> Option:
        return Option(Text(
            f"{'●' if session.id == self.active_id else ' '} {session.title}"
            f"  · {'archived' if session.closed else 'open'}"
            f"  · {len(session.messages)} messages"
        ), id=session.id)

    def compose(self) -> ComposeResult:
        with Vertical(id="session-picker"):
            yield Static("Sessions", id="session-picker-title")
            yield Input(placeholder="Search titles and content · regex · case-insensitive", id="session-picker-search")
            yield Static(f"{len(self.sessions)} sessions", id="session-picker-status", markup=False)
            yield OptionList(*[self._option(session) for session in self.sessions], id="session-picker-options")
            yield Static("Type to filter · ↑/↓ select · Enter open · Esc cancel", id="session-picker-note")
            yield Button("Cancel", id="session-picker-cancel", compact=True)

    def on_mount(self) -> None:
        options = self.query_one("#session-picker-options", OptionList)
        options.highlighted = 0 if self.sessions else None
        self.query_one("#session-picker-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self.is_running:
            return
        if event.input.id != "session-picker-search":
            return
        options = self.query_one("#session-picker-options", OptionList)
        selected = options.get_option_at_index(options.highlighted).id if options.highlighted is not None else None
        matches = []
        status = ""
        try:
            if len(event.value) > 512:
                raise ValueError("Pattern too long (maximum 512 characters)")
            pattern = regex.compile(event.value, regex.IGNORECASE | regex.MULTILINE) if event.value else None
            deadline = monotonic() + 0.1
            for session in self.sessions:
                if monotonic() > deadline:
                    raise TimeoutError
                if pattern is None or pattern.search(self._search_text[session.id], timeout=0.01):
                    matches.append(session)
            status = f"{len(matches)} of {len(self.sessions)} sessions" if matches else "No matching sessions"
        except (regex.error, ValueError) as error:
            matches = []
            status = f"Invalid regex: {error}"
        except TimeoutError:
            matches = []
            status = "Search timed out; simplify the regex"
        options.clear_options()
        options.add_options([self._option(session) for session in matches])
        ids = [session.id for session in matches]
        options.highlighted = ids.index(selected) if selected in ids else (0 if ids else None)
        self.query_one("#session-picker-status", Static).update(status)

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input) and event.key in {"up", "down"}:
            options = self.query_one("#session-picker-options", OptionList)
            if event.key == "down":
                options.action_cursor_down()
            else:
                options.action_cursor_up()
            event.prevent_default()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "session-picker-search":
            options = self.query_one("#session-picker-options", OptionList)
            if options.highlighted is not None:
                self.dismiss(options.get_option_at_index(options.highlighted).id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "session-picker-cancel":
            self.dismiss(None)


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

    def __init__(self, provider: str, current_model: str, config: ProviderConfig | None = None) -> None:
        super().__init__()
        self.provider = provider
        self.current_model = current_model
        self.config = config
        self._options = EXECUTOR_MODELS[provider]

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker"):
            yield Static(f"Choose {self.provider.title()} model", id="model-picker-title")
            yield Static(
                "Models come from the configured provider. Catalog entries may not all support text chat." if self.provider in API_PROVIDERS else "Availability depends on the account signed in to the provider CLI.",
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
        self.query_one("#model-picker-refresh", Button).label = f"Refresh available {self.provider.title()} models"
        self._show_custom_input()
        if self.provider in API_PROVIDERS:
            self._load_api_model_catalog()
        elif self.provider == "codex":
            self._load_codex_model_catalog()
        elif self.provider == "claude":
            self._load_claude_model_catalog()
        else:
            self.query_one("#model-picker-refresh", Button).display = False

    def _open_choices(self) -> None:
        selector = self.query_one("#model-picker-select", Select)
        selector.focus()
        selector.expanded = True

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
            if self.provider in API_PROVIDERS:
                self._load_api_model_catalog()
            elif self.provider == "claude":
                self._load_claude_model_catalog()
            elif self.provider == "codex":
                self._load_codex_model_catalog()
        elif button_id == "model-cancel":
            self.dismiss(None)

    @work(exclusive=True)
    async def _load_api_model_catalog(self) -> None:
        button = self.query_one("#model-picker-refresh", Button)
        selector = self.query_one("#model-picker-select", Select)
        button.disabled = selector.disabled = True
        button.label = "Loading model catalog…"
        try:
            self._options = await load_api_models(self.config)
            selector.set_options(self._options)
            selector.value = self._initial_choice()
            self._show_custom_input()
        except (OSError, ValueError, RuntimeError) as error:
            self.notify(f"Could not load models: {error}", severity="warning")
        finally:
            button.disabled = selector.disabled = False
            button.label = "Refresh available models"
            if self.query("#model-picker-select"):
                self._open_choices()

    @work(exclusive=True)
    async def _load_claude_model_catalog(self) -> None:
        button = self.query_one("#model-picker-refresh", Button)
        selector = self.query_one("#model-picker-select", Select)
        button.disabled = selector.disabled = True
        button.label = "Loading Claude model catalog…"
        try:
            self._options = await load_claude_model_catalog()
            selector.set_options(self._options)
            selector.value = self._initial_choice()
            self._show_custom_input()
        except (OSError, TimeoutError, ValueError, RuntimeError) as error:
            self.notify(f"Could not load Claude models; showing aliases: {error}", severity="warning")
        finally:
            button.disabled = selector.disabled = False
            button.label = "Refresh available Claude models"
            if self.query("#model-picker-select"):
                self._open_choices()

    @work(exclusive=True)
    async def _load_codex_model_catalog(self) -> None:
        button = self.query_one("#model-picker-refresh", Button)
        selector = self.query_one("#model-picker-select", Select)
        selector.disabled = True
        button.disabled = True
        button.label = "Loading Codex model catalog…"
        try:
            self._options = await load_codex_model_catalog()
            selector = self.query_one("#model-picker-select", Select)
            selector.set_options(self._options)
            selector.value = self._initial_choice()
            self._show_custom_input()
            self.notify(f"Loaded {len(self._options) - 2} Codex models from the installed CLI.")
        except (FileNotFoundError, OSError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            self.notify(f"Could not load Codex models: {error}", severity="warning")
        finally:
            selector.disabled = False
            button.disabled = False
            button.label = "Refresh all available Codex models"
            if self.query("#model-picker-select"):
                self._open_choices()


class SkillPickerScreen(ModalScreen[str | None]):
    CSS = """
    SkillPickerScreen { align: center middle; background: #000000aa; }
    #skill-picker { width: 76; max-height: 90%; padding: 1 2; border: tall #f03c95; background: #161616; }
    #skill-picker Button { height: 1; margin-top: 1; }
    """

    def __init__(self, skills) -> None:
        super().__init__()
        self.skills = skills

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-picker"):
            yield Static("Local skills · Claude and Codex")
            yield Static("Select a skill, then enter your task. Nothing runs until you submit.")
            yield Select([(skill.key, skill.key) for skill in self.skills],
                         allow_blank=False, id="skill-choice")
            yield Static("", id="skill-description")
            yield Button("Use selected skill", id="skill-use", compact=True)
            yield Button("Cancel", id="skill-cancel", compact=True)

    def on_mount(self) -> None:
        selector = self.query_one("#skill-choice", Select)
        selector.focus()
        selector.expanded = True

    def on_select_changed(self, event: Select.Changed) -> None:
        skill = next((skill for skill in self.skills if skill.key == event.value), None)
        if skill:
            self.query_one("#skill-description", Static).update(
                f"{skill.description}\n{skill.path}\nOnly invoke skills you trust."
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "skill-use":
            self.dismiss(str(self.query_one("#skill-choice", Select).value))
        elif event.button.id == "skill-cancel":
            self.dismiss(None)


class ApprovalScreen(ModalScreen[bool]):
    """Explicit user approval; Escape and initial focus always deny."""

    BINDINGS = [Binding("escape", "deny", "Deny", priority=True)]
    DEFAULT_CSS = """
    ApprovalScreen { align: center middle; }
    #approval-dialog { width: 80%; height: auto; max-height: 85%; border: solid $warning; padding: 1 2; }
    #approval-dialog Button { height: 1; min-width: 16; margin: 1 2 0 0; border: none; }
    """

    def __init__(self, title: str, detail: str):
        super().__init__()
        self.title_text, self.detail = title, detail

    def compose(self):
        with VerticalScroll(id="approval-dialog"):
            yield Static(Text(self.title_text + "\n\n" + self.detail))
            with Horizontal():
                yield Button("Deny", id="approval-deny")
                yield Button("Approve once", id="approval-allow", variant="warning")

    def on_mount(self):
        self.query_one("#approval-deny", Button).focus()

    def action_deny(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "approval-allow")


class DenniceApp(App[None]):
    """Thin Textual control surface over Harness.run_events()."""

    TITLE = "Dennice"
    BINDINGS = [
        Binding("ctrl+n", "new_session", "New session", priority=True),
        Binding("ctrl+x", "cancel_run", "Stop run", priority=True),
        ("r", "run_task", "Run task"),
        ("c", "classify_task", "Classify"),
        ("b", "run_benchmark", "Benchmark"),
        Binding("ctrl+s", "setup", "Setup", priority=True),
        Binding("ctrl+p", "command_help", "Commands", priority=True),
        Binding("ctrl+d", "toggle_details", "Details", priority=True),
        Binding("ctrl+shift+c", "copy_response", "Copy response", priority=True),
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
    #home-sessions-heading { margin-top: 1; color: #b6c6dd; height: 1; }
    #home-session-list { height: auto; max-height: 8; min-height: 2; border: none; background: #141c28; }
    #home-session-search { height: 1; min-height: 1; border: none; padding: 0 1; background: #273346; color: #f1f5ff; margin: 1 0; }
    #home-session-search:focus { background: #304f75; }
    #home-session-search-status { height: auto; color: #a9bbd2; }
    #file-git-branch { height: 1; color: #81aaff; }
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
    #run-status { height: 1; margin: 0 4; }
    #copy-response { height: 1; margin: 0 4; }
    #output {
        height: auto;
        margin: 1 2;
        padding: 1 2;
        background: #090909;
    }
    #transcript-scroll { height: 1fr; overflow-y: auto; }
    .detail-panel {
        border: round $accent;
        margin: 0 1 1 1;
        padding: 1;
        height: 1fr;
        overflow: auto;
        background: $panel;
    }
    #statusline {
        height: auto;
        min-height: 1;
        padding: 0 1;
        color: #717171;
        background: #090909;
    }
    #footer { display: none; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.harness = Harness.from_config()
        self._launch_directory = Path.cwd().resolve()
        self.harness.approve = self._approve_tool
        self._goals = GoalController(self.harness.config.runs.path)
        self._activity_frame = 0
        self._run_is_active = False
        self._execution_worker = None
        self._active_goal_id: str | None = None
        self._running_session_id: str | None = None
        self._activity_detail = ""
        self._session_store = SessionStore(self.harness.config.runs.path, str(self._launch_directory))
        self._sessions = self._session_store.list()
        self._active_session_index: int | None = 0 if self._sessions else None
        self._command_matches: list[tuple[str, str]] = []
        self._command_selection = 0
        self._history_cursor: int | None = None
        self._history_draft = ""
        self._home_session_ids: list[str] = []
        self._git_branches: dict[str, str] = {}

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
                yield Static("", id="home-attachments")
                yield Static("", id="home-command-menu", classes="command-menu")
                yield Static(
                    "Run  ·  Route  ·  Benchmark  ·  Setup",
                    id="home-modes",
                )
                yield Static(
                    "ctrl+enter run   enter newline   ! command terminal   /help commands   ctrl+n new session",
                    id="home-help",
                )
                yield Static("Previous sessions · click to open", id="home-sessions-heading")
                yield FileInput(placeholder="Search session titles + content (regex)", id="home-session-search")
                yield Static("", id="home-session-search-status", markup=False)
                yield OptionList(id="home-session-list")
        with Vertical(id="workspace"):
            with Horizontal(id="session-tabs"):
                for index in range(8):
                    yield Button("", id=f"session-tab-{index}", classes="session-tab", compact=True)
                    yield Button("×", id=f"session-close-{index}", classes="session-close", compact=True)
            with Horizontal(id="workspace-main"):
                yield WorkspaceFiles(self._launch_directory, id="workspace-files")
                with Vertical(id="details"):
                    with Vertical(classes="detail-panel"):
                        yield Static("Cognitive routing\nAwaiting task.", id="routing")
                    with Vertical(classes="detail-panel"):
                        yield Static("Tools / events\nAwaiting task.", id="events")
                with Vertical(id="agent"):
                    with VerticalScroll(id="transcript-scroll"):
                        yield Transcript("", id="output")
                    yield FilePreviewPane(id="session-file-preview")
                    yield GitDiffPane(id="session-git-diff")
                    yield Static("", id="run-status")
                    yield Button("Copy response · Ctrl+Shift+C", id="copy-response", compact=True)
            yield TaskComposer(
                placeholder="Describe the next task…  Ctrl+Enter runs · ! command opens terminal mode",
                id="workspace-task",
            )
            yield Static("", id="workspace-terminal-mode", classes="terminal-mode-label")
            yield Static("", id="workspace-attachments")
            yield Static("", id="workspace-command-menu", classes="command-menu")
        yield Static(self._status_text(), id="statusline", markup=False)
        yield Footer(id="footer")

    def on_mount(self) -> None:
        self.query_one("#home-task", TaskComposer).focus()
        self._show_attachments()
        self.set_interval(0.12, self._animate_activity)
        self._recover_workspace()
        self._render_session_tabs()
        if self._active_session_index is not None:
            self._restore_session_files()

    @work(group="recovery", exclusive=True)
    async def _recover_workspace(self) -> None:
        recovered = await self.harness.store.recover_stale()
        if recovered:
            self.notify(f"Recovered {len(recovered)} interrupted run(s). Use /recovery to inspect; nothing was replayed.", severity="warning")

    @work(group="recovery", exclusive=True)
    async def _recovery_command(self, argument: str) -> None:
        try:
            parts = shlex.split(argument)
            if not parts or parts == ["list"]:
                await self.harness.store.recover_stale()
                rows = await self.harness.store.list_recent(100)
                listings = []
                for row in rows:
                    inspection = await self.harness.store.inspect_recovery(row.run_id)
                    listings.append(f"{row.run_id} · {inspection['status']} · {len(inspection['unresolved_effects'])} unresolved effects")
                text = "\n".join(listings)
                self._show_local_message("Recovery · nothing is replayed\n" + (text or "No saved runs.") +
                    "\n/recovery inspect <run-id>\n/recovery reconcile <run-id> <operation-id> <completed|not_executed|compensated> <evidence notes>")
            elif len(parts) == 2 and parts[0] == "inspect":
                result = await self.harness.store.inspect_recovery(parts[1])
                self._show_local_message(json.dumps(result, indent=2, ensure_ascii=False))
            elif len(parts) >= 5 and parts[0] == "reconcile":
                result = await self.harness.store.reconcile(parts[1], parts[2], parts[3], " ".join(parts[4:]))
                self._show_local_message("User evidence recorded; no action replayed.\n" + json.dumps(result, indent=2, ensure_ascii=False))
            else:
                self._show_local_message("Usage: /recovery list | inspect <run-id> | reconcile <run-id> <operation-id> <completed|not_executed|compensated> <evidence notes>")
        except (ValueError, RuntimeError, FileNotFoundError) as error:
            self._show_local_message(f"Recovery: {error}")
    def submit_composer(self, composer: TaskComposer) -> None:
        """Submit a multiline composer explicitly; Enter itself remains a newline."""
        value = composer.value.strip()
        if not value and self._ensure_active_session().pending_images:
            value = "Describe the attached image."
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

    @work(group="clipboard", exclusive=True)
    async def paste_clipboard_image(self, composer: TaskComposer) -> None:
        session = self._ensure_active_session()
        try:
            path = await asyncio.to_thread(paste_image, self._launch_directory / ".dennice/attachments")
            if path:
                self._attach_image(str(path), session)
            else:
                composer.action_paste()
        except (OSError, ValueError, NotImplementedError) as error:
            self.notify(f"Clipboard image unavailable: {error}. Use /attach <path> instead.", severity="warning")
            composer.action_paste()

    def _attach_image(self, path: str, session: ChatSession | None = None) -> None:
        session = session or self._ensure_active_session()
        try:
            source = Path(path.strip().strip('"').strip("'")).expanduser()
            if not source.is_absolute():
                source = self._session_directory(session) / source
            validated = str(validate_image(source))
        except (OSError, ValueError) as error:
            self.notify(f"Cannot attach image: {error}", severity="warning")
            return
        session = session or self._ensure_active_session()
        if len(session.pending_images) >= 8:
            self.notify("Limit: eight images per prompt.", severity="warning")
            return
        if validated not in session.pending_images:
            session.pending_images.append(validated)
        self._show_attachments()
        self.notify("Image attached locally. It will be sent when you submit.")

    def _show_attachments(self) -> None:
        paths = self._sessions[self._active_session_index].pending_images if self._active_session_index is not None else []
        label = "Images: " + ", ".join(Path(path).name for path in paths) + " · /clear-images to remove" if paths else ""
        for widget_id in ("#home-attachments", "#workspace-attachments"):
            self.query_one(widget_id, Static).update(label)
            self.query_one(widget_id).display = bool(paths)

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
        if event.input.id == "home-session-search":
            if not self.is_running:
                return
            self._filter_home_sessions(event.value)
            return
        if event.input.id not in {"home-task", "workspace-task"} or isinstance(self.screen, ModalScreen):
            return
        self._refresh_statusline()
        menu_id = "#home-command-menu" if event.input.id == "home-task" else "#workspace-command-menu"
        if self.query(menu_id):
            self._show_command_menu(menu_id, event.value)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if not self.is_running:
            return
        if event.text_area.id not in {"home-task", "workspace-task"} or isinstance(self.screen, ModalScreen):
            return
        self._refresh_statusline()
        menu_id = "#home-command-menu" if event.text_area.id == "home-task" else "#workspace-command-menu"
        if self.query(menu_id):
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
        if isinstance(self.screen, ModalScreen):
            self.notify("Close the current dialog before opening a new session.")
            return
        if self._file_editor_dirty():
            self.notify("Save or discard the open file edits before switching sessions.", severity="warning")
            return
        if len(self._sessions) >= 8:
            self.notify("Close a tab before opening another session (8 open tabs maximum).")
            return
        self._save_sessions()
        session_number = len(self._sessions) + 1
        self._sessions.append(
            ChatSession.new(f"Session {session_number}", str(self._launch_directory))
        )
        self._active_session_index = len(self._sessions) - 1
        self._history_cursor = None
        self._history_draft = ""
        self._run_is_active = False
        self._activate_workspace("")
        self._show_transcript()
        self._render_session_tabs()
        self._save_sessions()
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
        self.harness = Harness(config, hooks=self.harness.hooks, mcp=self.harness.mcp, approve=self._approve_tool)
        self.query_one("#masthead-copy", Static).update(self._masthead_text())
        self.query_one("#statusline", Static).update(self._status_text())
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
        if command in {"hooks", "mcp"}:
            self._extension_command(command, argument)
        elif command == "recovery":
            self._recovery_command(argument)
        elif command == "cwd":
            self._cwd_command(argument)
        elif command == "routing":
            self._routing_command(argument)
        elif command == "pool":
            self._pool_command(argument)
        elif command == "tools":
            self._tools_command(argument)
        elif command == "goal":
            self._goal_command(argument)
        elif command in {"help", "commands"}:
            self._show_command_help()
        elif command == "new":
            self.action_new_session()
        elif command == "session":
            self._select_session_from_command(argument)
        elif command == "sessions":
            self._list_sessions()
        elif command == "resume":
            self._resume_session(argument)
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
        elif command == "compact":
            self._compact_session()
        elif command == "skills":
            self._open_skill_picker()
        elif command == "skill":
            self._run_skill(argument)
        elif command == "attach":
            self._attach_image(argument)
        elif command == "clear-images":
            self._ensure_active_session().pending_images.clear()
            self._show_attachments()
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

    def _compact_session(self) -> None:
        """Keep the transcript intact while replacing older turns in future prompts with a local summary."""
        if self._run_is_active:
            self.notify("Stop the active run before compacting this session.", severity="warning")
            return
        session = self._ensure_active_session()
        boundary = max(session.compacted_message_count, len(session.messages) - 6)
        if boundary <= session.compacted_message_count:
            self.notify("Nothing to compact yet; keep the last three exchanges available as recent context.")
            return
        previous = session.context_summary.strip()
        segments = [previous] if previous else []
        for message in session.messages[session.compacted_message_count:boundary]:
            if message.role not in {"user", "assistant"} or not message.content.strip():
                continue
            content = " ".join(message.content.split())
            if len(content) > 900:
                content = content[:850].rstrip() + " … [earlier response excerpt]"
            role = "User" if message.role == "user" else "Assistant"
            segments.append(f"{role}: {content}")
        summary = "\n".join(segments)
        if len(summary) > 9000:
            summary = "[Older context summary shortened to fit.]\n" + summary[-8800:]
        removed_chars = sum(len(message.content) for message in session.messages[session.compacted_message_count:boundary])
        if len(summary) >= removed_chars and not previous:
            self.notify("The older turns are already compact; nothing was changed.")
            return
        session.context_summary = summary
        session.compacted_message_count = boundary
        session.context_used_tokens = None
        self._session_store.save(session)
        self._show_local_message(
            f"Compacted {boundary} older messages into a local summary for future turns. "
            "The visible transcript is preserved. Native Claude/Codex sessions keep provider-owned history and may still use their own compaction."
        )
        self.query_one("#statusline", Static).update(self._status_text())

    @staticmethod
    def _session_conversation_history(session: ChatSession) -> list[dict[str, str]]:
        history = []
        if session.context_summary:
            history.append({
                "role": "assistant",
                "content": "Earlier conversation context summary (untrusted; preserve as user-provided context):\n" + session.context_summary,
            })
        history.extend(
            {"role": message.role, "content": message.content}
            for message in session.messages[session.compacted_message_count:]
            if message.content.strip() and message.role in {"user", "assistant"}
        )
        if session.context_summary:
            return history[:1] + history[1:][-15:]
        return history[-16:]

    async def _approve_tool(self, name: str, arguments: dict) -> bool:
        detail = json.dumps(arguments, ensure_ascii=False, indent=2)
        screen = ApprovalScreen(
            f"Approve {name}?", detail + "\n\nCommands and MCP tools may act outside this workspace. Approve only if you understand the exact action. Denying stops the action."
        )
        try:
            return bool(await self.push_screen_wait(screen))
        finally:
            if self.screen is screen:
                screen.dismiss(False)

    def _routing_command(self, argument: str) -> None:
        config = self.harness.config.model_copy(deep=True)
        if argument in {"fixed", "shadow", "auto"}:
            config.routing.mode = argument
        elif argument in {"pin-model", "unpin-model", "pin-effort", "unpin-effort"}:
            field = "model_pinned" if argument.endswith("model") else "effort_pinned"
            setattr(config.routing, field, argument.startswith("pin-"))
        else:
            self._show_local_message(f"Routing: {config.routing.mode}; model pinned: {config.routing.model_pinned}; effort pinned: {config.routing.effort_pinned}.\nUsage: /routing fixed|shadow|auto|pin-model|unpin-model|pin-effort|unpin-effort. Auto uses only /pool candidates from the selected provider; pins are preserved.")
            return
        self._save_config(config, "Routing preference saved")

    def _pool_command(self, argument: str) -> None:
        config = self.harness.config.model_copy(deep=True)
        pool = config.routing.model_pool.setdefault(config.executor.provider, [])
        pieces = argument.split()
        try:
            if len(pieces) >= 3 and pieces[0] == "add":
                attributes = {"model": pieces[1], "tier": pieces[2]}
                for flag in pieces[3:]:
                    if flag in {"tools", "vision"}:
                        attributes[flag] = True
                    elif flag.startswith("context="):
                        attributes["context_tokens"] = int(flag.split("=", 1)[1])
                    elif flag.startswith("efforts="):
                        attributes["efforts"] = flag.split("=", 1)[1].split(",")
                    else:
                        raise ValueError("Unknown capability flag: " + flag)
                candidate = ModelCandidate(**attributes)
                if any(item.model == candidate.model for item in pool):
                    raise ValueError("Model is already in the pool")
                pool.append(candidate)
                self._save_config(config, "Routing model approved with explicitly declared capabilities; unmentioned capabilities remain conservative.")
            elif len(pieces) == 2 and pieces[0] == "remove":
                config.routing.model_pool[config.executor.provider] = [item for item in pool if item.model != pieces[1]]
                self._save_config(config, "Routing model removed")
            else:
                self._show_local_message("Approved models for " + config.executor.provider + "\n" + "\n".join(f"{item.model}: {item.tier}, tools={item.tools}, vision={item.vision}, context={item.context_tokens}, efforts={','.join(e.value for e in item.efforts)}" for item in pool) + "\nUsage: /pool add <exact-model-id> lightweight|balanced|strong [tools] [vision] [context=8192] [efforts=low,medium,high]; /pool remove <id>. Declare only capabilities your provider actually supports. No availability or capability is inferred from names.")
        except ValueError as error:
            self._show_local_message(str(error))

    def _tools_command(self, argument: str) -> None:
        if argument not in {"on", "off"}:
            self._show_local_message("Usage: /tools on|off. Native tools apply to API/local executors only. Mutations require individual approval. CLI providers manage their own tools.")
            return
        config = self.harness.config.model_copy(deep=True)
        config.tools.enabled = argument == "on"
        self._save_config(config, f"Native tools {argument}; root: {Path(config.tools.root).resolve()}")

    @work(group="extension-management", exclusive=True)
    async def _extension_command(self, kind: str, argument: str) -> None:
        pieces = argument.split()
        collection = self.harness.config.hooks if kind == "hooks" else self.harness.config.mcp
        manager = self.harness.hooks if kind == "hooks" else self.harness.mcp
        root = str(self._session_directory(self._ensure_active_session()))
        if not pieces or pieces == ["list"]:
            self._show_local_message(kind.upper() + "\n" + "\n".join(f"{item.name}: {'enabled' if item.enabled else 'disabled'}, {'trusted' if manager.is_trusted(item, root) else 'not trusted in this directory'}" for item in collection) + "\nConfigure entries in dennice.yaml. Use /" + kind + " trust|enable|disable" + ("|test" if kind == "mcp" else "") + " <name>. Trust is directory-scoped and expires when the app closes or configuration changes.")
            return
        if len(pieces) != 2 or not any(item.name == pieces[1] for item in collection):
            self._show_local_message("Unknown configured entry. Use /" + kind + " list.")
            return
        action, name = pieces
        item = next(item for item in collection if item.name == name)
        if action == "trust":
            # Show the exact launch settings, never credential values.
            if await self._approve_tool(f"trust {kind}/{name} for this launch and directory", {"cwd": root, **item.model_dump(mode="json")}):
                manager.trust(item, root=root)
                self._show_local_message(f"Trusted {name} in {root} for this launch. Nothing was launched.")
        elif action in {"enable", "disable"}:
            config = self.harness.config.model_copy(deep=True)
            entries = config.hooks if kind == "hooks" else config.mcp
            next(entry for entry in entries if entry.name == name).enabled = action == "enable"
            self._save_config(config, f"{name} {action}d; execution-setting changes require fresh trust")
        elif kind == "mcp" and action == "test":
            if not item.enabled or not manager.is_trusted(item, root):
                self._show_local_message("Enable the server, then explicitly trust its current settings before testing.")
                return
            self._show_local_message(f"Connecting to {name}…")
            try:
                async with manager.connect([item], local_only=self.harness.config.privacy.local_only, root=root) as connections:
                    status = connections.status[-1]
                    self._show_local_message(
                        f"Success: {name} connected; "
                        f"{status['approved_tools']} approved tools, "
                        f"{status['approved_resources']} approved resources, and "
                        f"{status['approved_prompts']} approved prompts discovered. "
                        "No tools invoked and no resource or prompt content fetched."
                    )
            except Exception as error:
                self._show_local_message(f"Failed: {name}: {type(error).__name__}. Check server configuration; secrets are not displayed.")
        else:
            self._show_local_message("Unsupported management action")

    def _goal_command(self, argument: str) -> None:
        session = self._ensure_active_session()
        if argument in {"status", "pause", "resume", "cancel"}:
            if not session.goal_id:
                self._show_local_message("This session has no goal. Usage: /goal <objective>")
                return
            goal = self._goals.get(session.goal_id)
            if argument in {"pause", "cancel"}:
                goal.status = "paused" if argument == "pause" else "cancelled"
                self._goals.save(goal)
                if self._active_goal_id == goal.id:
                    self.action_cancel_run()
            elif argument == "resume":
                if goal.status in {"complete", "cancelled", "budget_exhausted", "running"}:
                    self._show_local_message("Goal cannot resume from " + goal.status + "; create a new explicit goal if needed.")
                    return
                self._start_run(goal.objective, goal=goal)
                return
            self._show_local_message(f"Goal {goal.status}: {goal.objective}\n{goal.detail}\nRuns: {len(goal.runs)}/{goal.max_runs}; tokens reported: {goal.tokens_used}; elapsed: {goal.elapsed_seconds:.1f}s")
        elif argument:
            if self._execution_worker is not None and not self._execution_worker.is_finished:
                self.notify("Stop the active run before creating a goal.")
                return
            if session.goal_id and self._goals.get(session.goal_id).status not in {"complete", "cancelled", "budget_exhausted"}:
                self._show_local_message("An unfinished goal exists. Use /goal status, /goal resume or /goal cancel before replacing it.")
                return
            goal = GoalState(session_id=session.id, objective=argument)
            self._goals.save(goal)
            session.goal_id = goal.id
            self._session_store.save(session)
            self._start_run(argument, goal=goal)
        else:
            self._show_local_message("Usage: /goal <objective> or /goal status|pause|resume|cancel. Configure verification.required_files or verification.commands first. Goals stop for user input after uncertain failures; no automatic effect replay.")

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
        self._save_sessions()
        sessions = self._session_store.list(include_closed=True)
        if not sessions:
            self._show_local_message("No saved sessions yet. Use /new to start one.")
            return
        active_id = self._sessions[self._active_session_index].id if self._active_session_index is not None else None
        self.push_screen(SessionPickerScreen(sessions, active_id), self._session_chosen)

    def _session_chosen(self, session_id: str | None) -> None:
        if session_id is None:
            return
        for index, session in enumerate(self._sessions):
            if session.id == session_id:
                self._select_session(index)
                return
        self._resume_session(session_id)

    def _rename_current_session(self, title: str) -> None:
        normalized = " ".join(title.split())
        if not normalized:
            self._show_local_message("Usage: /rename <title>")
            return
        session = self._ensure_active_session()
        session.title = normalized[:24] + ("…" if len(normalized) > 24 else "")
        self._activate_workspace("")
        self._render_session_tabs()
        self.query_one(f"#session-tab-{self._active_session_index}", Button).refresh(layout=True)
        self.notify(f"Renamed session to {session.title}")

    def _resume_session(self, session_id: str) -> None:
        if len(self._sessions) >= 8:
            self.notify("Close a tab before reopening another session.")
            return
        for session in self._session_store.list(include_closed=True):
            if session.id == session_id and session.closed:
                session.closed = False
                self._sessions.append(session)
                self._select_session(len(self._sessions) - 1)
                return
        self._show_local_message("No archived session with that ID. Use /sessions to list IDs.")

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
        config.routing.model_pinned = True
        self._save_config(config, f"Model set to {value}")

    def _open_model_picker(self) -> None:
        executor = self.harness.config.executor
        if executor.provider not in EXECUTOR_MODELS:
            self._show_local_message("Select Codex or Claude in /setup before choosing a model.")
            return
        self.push_screen(
            ModelPickerScreen(executor.provider, executor.model, executor.model_copy(deep=True)),
            self._apply_model_picker,
        )

    def _open_skill_picker(self) -> None:
        skills = discover_skills(self._session_directory(self._ensure_active_session()))
        if not skills:
            self._show_local_message("No local skills found. Add a SKILL.md under .agents/skills or .claude/skills.")
            return
        self.push_screen(SkillPickerScreen(skills), self._prepare_skill)

    def _prepare_skill(self, key: str | None) -> None:
        if key:
            composer = self._active_task_input()
            composer.value = f"/skill {key} "
            composer.focus()

    def _run_skill(self, argument: str) -> None:
        provider = self.harness.config.executor.provider
        if provider not in {"codex", "claude", "copilot", *API_PROVIDERS}:
            self._show_local_message("Select a supported executor in /setup before running a skill.")
            return
        if provider in API_PROVIDERS and not self.harness.config.tools.enabled:
            self._show_local_message("API/local skills require /tools on. The selected manifest is supplied as user context; referenced resources outside the approved workspace are unavailable. Scripts are not automatically run.")
            return
        key, _, task = argument.partition(" ")
        if not task.strip():
            self._show_local_message("Usage: /skill <name or qualified key> <task>. Use /skills to browse.")
            return
        matches = [skill for skill in discover_skills(self._session_directory(self._ensure_active_session())) if key in {skill.key, skill.name}]
        if len(matches) != 1:
            self._show_local_message("Skill not found or name is ambiguous. Use /skills to choose a qualified key.")
            return
        skill = matches[0]
        self._start_run(task, selected_skill={"name": skill.name, "provider": skill.provider, "path": str(skill.path)})

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
        config.routing.effort_pinned = True
        self._save_config(config, f"Effort set to {normalized}")

    def _set_permission_mode(self, value: str) -> None:
        normalized = {"read-write": "workspace-write"}.get(value.lower(), value.lower())
        try:
            permission = PermissionMode(normalized)
        except ValueError:
            self._show_local_message("Usage: /permissions <read-only|read-write|plan>")
            return
        config = self.harness.config.model_copy(deep=True)
        if config.executor.provider == "mock":
            self._show_local_message("Select a supported executor in /setup before setting permissions.")
            return
        config.executor.permission_mode = permission
        self._save_config(config, f"Permissions set to {permission.value}")

    def _save_config(self, config: DenniceConfig, message: str) -> None:
        config.save()
        self.harness = Harness(config, hooks=self.harness.hooks, mcp=self.harness.mcp, approve=self._approve_tool)
        self.query_one("#masthead-copy", Static).update(self._masthead_text())
        self.query_one("#statusline", Static).update(self._status_text())
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
        if "<task>" in command or command.startswith(("/skill ", "/attach ")):
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
        self._ensure_active_session().messages.append(ChatMessage("system", message))
        self._show_transcript()

    def _start_run(self, task: str, selected_skill: dict | None = None, goal: GoalState | None = None) -> None:
        if self._execution_worker is not None and not self._execution_worker.is_finished:
            self.notify("A run is active. Press Ctrl+X to stop it before submitting another.")
            return
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
            history = self._session_conversation_history(session)
            request = Task(
                prompt=task,
                context={"conversation_history": history} if history else {},
                metadata={"session_id": session.id},
            )
            images = list(dict.fromkeys([
                *(path for message in session.messages[-16:] for path in message.images),
                *session.pending_images,
            ]))[-8:]
            if images:
                request.context["images"] = images
            if selected_skill:
                request.context["selected_skill"] = selected_skill
            if session.context_summary:
                request.context["compaction_summary"] = session.context_summary
            assistant_message = ChatMessage("assistant", "")
            session.messages.extend((ChatMessage("user", task, images=list(session.pending_images)), assistant_message))
            session.pending_images.clear()
            self._show_attachments()
            if session.title.startswith("Session "):
                session.title = self._session_title(task)
            self._activate_workspace(task)
            self._render_session_tabs()
            self._active_goal_id = goal.id if goal else None
            self._execution_worker = self._run(request, assistant_message, session, goal)

    def action_cancel_run(self) -> None:
        if self._execution_worker is not None and not self._execution_worker.is_finished:
            self._execution_worker.cancel()
            self.notify("Stopping the active run…")

    def _start_terminal_command(self, command: str) -> None:
        """Run an explicit user terminal request in the project directory.

        Unlike an agent tool call, a command prefixed by ``!`` is direct user
        input. It is still kept out of model conversation history and clearly
        labelled in the transcript.
        """
        if not command:
            self._show_local_message("Usage: ! <bash command>\n\nExample: ! git status --short")
            return
        if self._execution_worker is not None and not self._execution_worker.is_finished:
            self.notify("A run is active. Press Ctrl+X to stop it before running a command.")
            return
        session = self._ensure_active_session()
        terminal_message = ChatMessage("terminal", "Running terminal command…")
        session.messages.extend((ChatMessage("terminal", f"! {command}"), terminal_message))
        if session.title.startswith("Session "):
            session.title = self._session_title(command)
        self._activate_workspace("")
        self._render_session_tabs()
        self._execution_worker = self._run_terminal(command, terminal_message, session)

    @work(group="execution", exclusive=True)
    async def _run_terminal(
        self, command: str, terminal_message: ChatMessage, session: ChatSession
    ) -> None:
        """Capture a bounded Bash command result without making it an agent tool."""
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                command,
                cwd=str(self._session_directory(session)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **process_group_options(),
            )
            try:
                captured = await asyncio.wait_for(asyncio.gather(
                    read_bounded(process.stdout), read_bounded(process.stderr), process.wait()
                ), timeout=30)
                stdout, stderr = captured[:2]
            except TimeoutError:
                await stop_process(process)
                terminal_message.content = (
                    f"$ {command}\n\nCommand timed out after 30 seconds and was stopped."
                )
            else:
                output = stdout.decode("utf-8", errors="replace")
                error = stderr.decode("utf-8", errors="replace")
                rendered = (output + (f"\n[stderr]\n{error}" if error else "")).strip()
                if len(rendered) >= 24_000:
                    rendered = rendered[:24_000] + "\n\n[output truncated at 24 KB]"
                if not rendered:
                    rendered = "[no output]"
                status = "completed" if process.returncode == 0 else f"failed (exit {process.returncode})"
                terminal_message.content = f"$ {command}\n\n{rendered}\n\n[{status}]"
        except asyncio.CancelledError:
            terminal_message.content = f"$ {command}\n\n[Cancelled; external effects may already have occurred.]"
            raise
        except FileNotFoundError:
            terminal_message.content = (
                "Terminal mode needs Bash on PATH. On Windows, install Git Bash or WSL and restart Dennice."
            )
        except OSError as error:
            terminal_message.content = f"Terminal command could not start: {error}"
        finally:
            if process is not None:
                await stop_process(process)
            self._session_store.save(session)
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
        self._restore_session_files()
        self._refresh_statusline()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-response":
            self.action_copy_response()
        elif event.button.id and event.button.id.startswith("session-tab-"):
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
                ChatSession.new("Session 1", str(self._launch_directory))
            )
            self._active_session_index = 0
            self._render_session_tabs()
        return self._sessions[self._active_session_index]

    def _session_directory(self, session: ChatSession) -> Path:
        root = Path(session.working_directory or str(self._launch_directory))
        if not root.is_absolute():
            root = self._launch_directory / root
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Session working directory is not a directory.")
        return root

    def _session_harness(self, session: ChatSession) -> Harness:
        # Per-execution snapshots isolate a running session from tab switches.
        config = self.harness.config.model_copy(deep=True)
        config.tools.root = str(self._session_directory(session))
        return Harness(config, router=self.harness._router_override,
            executor=self.harness._executor_override, registry=self.harness.registry,
            composer=self.harness.composer, store=self.harness.store,
            hooks=self.harness.hooks, mcp=self.harness.mcp, approve=self.harness.approve)

    def _restore_session_files(self) -> None:
        self.query_one(FilePreviewPane).reset()
        self.query_one(GitDiffPane).display = False
        self.query_one("#transcript-scroll").display = True
        session = self._ensure_active_session()
        self.query_one("#workspace-files", WorkspaceFiles).set_workspace(
            Path(session.working_directory), session.id, session.file_filter,
            session.file_content_filter, session.file_replacement)
        self.query_one("#statusline", Static).update(self._status_text())
        self._refresh_git_branch(session.id, session.working_directory)

    def _file_editor_dirty(self) -> bool:
        panes = self.query(FilePreviewPane)
        return bool(panes and panes.first().has_unsaved_changes)

    @work(group="git-branch", exclusive=True)
    async def _refresh_git_branch(self, session_id: str, directory: str) -> None:
        process = None
        label = "Git: checking…"
        if not self.query("#file-git-branch"):
            return
        self.query_one("#file-git-branch", Static).update(label)
        try:
            env = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT", "WINDIR") if key in os.environ}
            env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
            process = await asyncio.create_subprocess_exec("git", "symbolic-ref", "--quiet", "--short", "HEAD",
                cwd=directory, env=env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, **process_group_options())
            output = await asyncio.wait_for(read_bounded(process.stdout, 1000), 2)
            code = await asyncio.wait_for(process.wait(), 2)
            branch = output.decode("utf-8", errors="replace").strip()
            if code == 0 and branch:
                label = "Git: " + "".join(c for c in branch if ord(c) >= 32 and ord(c) != 127)
            elif code == 1:
                label = "Git: detached HEAD"
            else:
                label = "Git: not a repository"
        except asyncio.CancelledError:
            raise
        except Exception:
            label = "Git: unavailable"
        finally:
            if process is not None:
                await stop_process(process)
        self._git_branches[directory] = label
        current = self._sessions[self._active_session_index] if self._active_session_index is not None else None
        if current and current.id == session_id and current.working_directory == directory and self.query("#statusline") and self.query("#file-git-branch") and not isinstance(self.screen, ModalScreen):
            self.query_one("#file-git-branch", Static).update(label)
            self.query_one("#statusline", Static).update(self._status_text())

    def on_workspace_files_filter_changed(self, event: WorkspaceFiles.FilterChanged) -> None:
        session = next((s for s in self._sessions if s.id == event.session_id), None)
        if session is None:
            return
        if event.path_filter is not None:
            session.file_filter = event.path_filter
        if event.content_filter is not None:
            session.file_content_filter = event.content_filter
        if event.replacement is not None:
            session.file_replacement = event.replacement
        if self.is_running and not isinstance(self.screen, ModalScreen) and self.query("#workspace-files"):
            sidebar = self.query_one("#workspace-files", WorkspaceFiles)
            if sidebar.session_id == session.id:
                sidebar.content_filter, sidebar.replacement = session.file_content_filter, session.file_replacement
        self._session_store.save(session)

    def on_workspace_files_preview_requested(self, event: WorkspaceFiles.PreviewRequested) -> None:
        event.stop()
        if self._ensure_active_session().id != event.session_id:
            return
        if self._file_editor_dirty():
            self.notify("Save or discard the open file edits before opening another file.", severity="warning")
            return
        self.query_one(GitDiffPane).display = False
        self.query_one("#transcript-scroll").display = False
        self.query_one(FilePreviewPane).open_file(event.snapshot, event.line, files=event.files, edit=event.edit)

    def on_file_preview_pane_closed(self, event: FilePreviewPane.Closed) -> None:
        event.stop()
        self.query_one("#transcript-scroll").display = True
        self.query_one(Transcript).focus(scroll_visible=False)

    def on_workspace_files_git_diff_requested(self, event: WorkspaceFiles.GitDiffRequested) -> None:
        event.stop()
        if self._ensure_active_session().id != event.session_id:
            return
        if self._file_editor_dirty():
            self.notify("Save or discard the open file edits before opening a Git diff.", severity="warning")
            return
        pane = self.query_one(GitDiffPane)
        try:
            pane.open_diff(event.files, event.entry, staged=event.staged)
        except (OSError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self.query_one(FilePreviewPane).display = False
        self.query_one("#transcript-scroll").display = False

    def on_git_diff_pane_closed(self, event: GitDiffPane.Closed) -> None:
        event.stop()
        self.query_one("#workspace-files", WorkspaceFiles).dismiss_git_diff()
        self.query_one("#transcript-scroll").display = True
        self.query_one(Transcript).focus(scroll_visible=False)

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input) and self.focused.id == "home-session-search":
            listing = self.query_one("#home-session-list", OptionList)
            if event.key in {"up", "down", "enter"}:
                if event.key == "down":
                    listing.action_cursor_down()
                elif event.key == "up":
                    listing.action_cursor_up()
                elif listing.highlighted is not None:
                    self._session_chosen(self._home_session_ids[listing.highlighted])
                event.prevent_default()
                event.stop()

    def _cwd_command(self, argument: str) -> None:
        session = self._ensure_active_session()
        if not argument:
            self._show_local_message(f"Working directory: {session.working_directory}\nUsage: /cwd <path>")
            return
        if self._execution_worker is not None and not self._execution_worker.is_finished:
            self.notify("Stop the active run before changing a session's working directory.")
            return
        if self._file_editor_dirty():
            self.notify("Save or discard the open file edits before changing folders.", severity="warning")
            return
        try:
            parts = shlex.split(argument)
            if len(parts) != 1:
                raise ValueError("Use one path; quote paths containing spaces.")
            path = Path(parts[0]).expanduser()
            if not path.is_absolute():
                path = self._session_directory(session) / path
            root = path.resolve(strict=True)
            if not root.is_dir():
                raise ValueError("Path is not a directory.")
            session.working_directory = str(root)
            session.file_filter = session.file_content_filter = session.file_replacement = ""
            self._session_store.save(session)
            self._restore_session_files()
            self.notify(f"Session directory: {root}")
        except (ValueError, OSError) as error:
            self.notify(f"Directory not changed: {error}", severity="warning")

    def on_workspace_files_open_folder(self, event: WorkspaceFiles.OpenFolder) -> None:
        event.stop()
        session = self._ensure_active_session()
        if session.id != event.session_id:
            return
        if self._execution_worker is not None and not self._execution_worker.is_finished:
            self.notify("Stop the active run before changing a session's working directory.")
            return
        session_id = session.id
        def chosen(directory):
            if directory is not None and self._ensure_active_session().id == session_id:
                self._cwd_command(shlex.quote(str(directory)))
        self.push_screen(DirectoryPicker(Path(session.working_directory)), chosen)

    def _select_session(self, index: int) -> None:
        if self._file_editor_dirty():
            self.notify("Save or discard the open file edits before switching sessions.", severity="warning")
            return
        self._save_sessions()
        self._active_session_index = index
        self._history_cursor = None
        self._history_draft = ""
        self._run_is_active = False
        self._activate_workspace("")
        self._render_session_tabs()
        self._show_transcript()

    def _close_session(self, index: int) -> None:
        """Archive one session without deleting its history."""
        if index == self._active_session_index and self._file_editor_dirty():
            self.notify("Save or discard the open file edits before closing this session.", severity="warning")
            return
        closing = self._sessions.pop(index)
        closing.closed = True
        self._session_store.save(closing)
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
        self._restore_session_files()

    def _save_sessions(self) -> None:
        for session in self._sessions:
            self._session_store.save(session)

    def on_unmount(self) -> None:
        self._save_sessions()

    def _render_session_tabs(self) -> None:
        self._save_sessions()
        if self.query("#home-session-list"):
            self._filter_home_sessions(self.query_one("#home-session-search", Input).value)
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

    def _filter_home_sessions(self, value: str) -> None:
        previous = self._session_store.list(include_closed=True)
        matches, status = [], ""
        try:
            if len(value) > 512:
                raise ValueError("Pattern too long (maximum 512 characters)")
            pattern = regex.compile(value, regex.IGNORECASE | regex.MULTILINE) if value else None
            deadline = monotonic() + .1
            for session in previous:
                if monotonic() > deadline:
                    raise TimeoutError
                text = session.title + "\n" + "\n".join(m.content for m in session.messages)
                if pattern is None or pattern.search(text, timeout=.01):
                    matches.append(session)
            status = f"{len(matches)} of {len(previous)} sessions" if matches else "No matching sessions"
        except (regex.error, ValueError) as error:
            matches, status = [], f"Invalid regex: {error}"
        except TimeoutError:
            matches, status = [], "Search timed out; simplify the regex"
        self._home_session_ids = [session.id for session in matches]
        listing = self.query_one("#home-session-list", OptionList)
        listing.clear_options()
        listing.add_options([Text(f"{s.title} · {'archived' if s.closed else 'open'} · {len(s.messages)} messages") for s in matches])
        listing.highlighted = 0 if matches else None
        listing.display = bool(matches)
        self.query_one("#home-session-search-status", Static).update(status)
        self.query_one("#home-sessions-heading", Static).update("Previous sessions · click to open" if previous else "No previous sessions yet")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "home-session-list" and event.option_index < len(self._home_session_ids):
            self._session_chosen(self._home_session_ids[event.option_index])

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

    @work(group="execution", exclusive=True)
    async def _run(
        self, task: Task, assistant_message: ChatMessage, session: ChatSession, goal: GoalState | None = None
    ) -> None:
        self.query_one("#events", Static).update("Tools / events\nStarting run...")
        self._run_is_active = True
        self._running_session_id = session.id
        self._activity_detail = "Starting"
        self._activity_frame = 0
        self._show_activity()
        events: list[str] = []
        usage_events = []
        output = ""
        try:
            execution_harness = self._session_harness(session)
            prior_goal_tokens = goal.tokens_used if goal else 0
            session.last_run_tokens = prior_goal_tokens if prior_goal_tokens else None
            session.last_run_budget_tokens = execution_harness.config.budgets.max_total_tokens
            session.last_run_usage_complete = False
            session.last_run_is_goal = goal is not None
            session.last_route_provider = None
            session.last_route_configured_model = None
            session.last_route_effective_model = None
            session.last_route_effective_effort = None
            self._refresh_statusline()
            stream = self._goals.run_events(goal, execution_harness, initial_task=task) if goal else execution_harness.run_events(task)
            async for event in stream:
                events.append(event.kind.value)
                if event.kind in {EventKind.MODEL_CALL_STARTED, EventKind.USAGE}:
                    usage_events.append(event)
                visible = (
                    self._active_session_index is not None
                    and self._sessions[self._active_session_index].id == session.id
                )
                if visible:
                    self.query_one("#events", Static).update("Tools / events\n" + "\n".join(events))
                if visible and event.kind == EventKind.ROUTING_COMPLETED:
                    self._show_routing_payload(event.payload["decision"])
                if event.kind == EventKind.GOAL_STATUS:
                    assistant_message.content = output + f"\n\nGoal {event.payload['status']}: {event.payload['detail']}"
                    self._show_transcript()
                if event.kind == EventKind.ROUTE_SELECTED:
                    session.last_route_provider = execution_harness.config.executor.provider
                    session.last_route_configured_model = execution_harness.config.executor.model
                    session.last_route_effective_model = str(event.payload["effective_model"])
                    session.last_route_effective_effort = event.payload.get("effective_effort")
                    if visible:
                        self.query_one("#events", Static).update("Tools / events\n" + json.dumps(event.payload, indent=2))
                        self._refresh_statusline()
                if event.kind == EventKind.MODEL_STREAM:
                    output += str(event.payload["text"])
                    assistant_message.content = output
                    self._show_transcript()
                    if visible:
                        self._refresh_statusline()
                if event.kind == EventKind.USAGE:
                    accounting = summarize_usage(usage_events)
                    executor_rows = [row for row in accounting["records"] if row["phase"] == "executor"]
                    known = [row["known_input_tokens"] + row["output_tokens"]
                             for row in executor_rows
                             if row.get("known_input_tokens") is not None and row["output_tokens"] is not None]
                    session.last_run_tokens = prior_goal_tokens + sum(known) if known or prior_goal_tokens else None
                    session.last_run_usage_complete = goal is None and bool(executor_rows) and all(
                        row["input_tokens"] is not None and row["output_tokens"] is not None
                        for row in executor_rows)
                    context_used = event.payload.get("context_tokens")
                    context_window = event.payload.get("context_window_tokens")
                    if type(context_used) is int and context_used >= 0:
                        session.context_used_tokens = context_used
                        session.context_provider = str(event.payload.get("provider") or self.harness.config.executor.provider)
                        model = event.payload.get("context_model") or event.payload.get("model") or self.harness.config.executor.model
                        session.context_model = str(model)
                    if type(context_window) is int and context_window > 0:
                        session.context_window_tokens = context_window
                    if visible:
                        self._refresh_statusline()
                if event.kind == EventKind.RUN_FAILED:
                    self._run_is_active = False
                    error = str(event.payload.get("error", "Unknown execution failure."))
                    partial = f"Partial response (unverified):\n\n{output.rstrip()}\n\n" if output.strip() else ""
                    assistant_message.content = (partial + "Execution stopped\n\n" + error
                        + "\n\nThe run trace was saved locally. Press Ctrl+D to inspect its event timeline.")
                    self._show_transcript()
                    self.notify(f"Execution failed in {session.title}; reopen it for details.", severity="error")
                if visible and event.kind == EventKind.RUN_COMPLETED:
                    label = ("Independent checks passed" if event.payload.get("verified") else
                             "Response produced; no independent completion check configured")
                    self.query_one("#events", Static).update(f"Run {event.run_id}\n{label}")
                if event.kind == EventKind.TOOL_STARTED:
                    self._activity_detail = "Tool: " + str(event.payload.get("tool", "working"))
                elif event.kind == EventKind.TOOL_COMPLETED:
                    self._activity_detail = "Continuing"
                elif event.kind == EventKind.ROUTING_STARTED:
                    self._activity_detail = "Classifying"
                elif event.kind == EventKind.EXECUTION_STARTED:
                    self._activity_detail = "Executing"
                elif event.kind == EventKind.VERIFICATION_COMPLETED:
                    self._activity_detail = "Verifying result"
                self._show_activity()
                self._session_store.save(session)
        except asyncio.CancelledError:
            assistant_message.content = output + "\n\n[Cancelled; external effects may already have occurred.]"
            raise
        except Exception as error:
            assistant_message.content = output + f"\n\nExecution stopped ({type(error).__name__}). Inspect the saved run trace before retrying."
        finally:
            self._run_is_active = False
            self._running_session_id = None
            if "execution_harness" in locals() and execution_harness._last_trace is not None:
                self.harness._last_trace = execution_harness._last_trace
            self._active_goal_id = None
            self._session_store.save(session)
            self._show_transcript()
            self._show_activity()
            self._refresh_statusline()

    def _animate_activity(self) -> None:
        if not self._run_is_active and not self._running_session_id:
            return
        self._activity_frame += 1
        self._show_activity()

    def _show_activity(self) -> None:
        if not self.is_running or isinstance(self.screen, ModalScreen) or not self.query("#run-status"):
            return
        running = self._execution_worker is not None and not self._execution_worker.is_finished
        status = self.query_one("#run-status", Static)
        if running and self._running_session_id:
            owner = next((s for s in self._sessions if s.id == self._running_session_id), None)
            label = self.harness.executor.id.capitalize()
            text = _activity_renderable(label, self._activity_frame)
            text.append(f" · {self._activity_detail}", style="#aaaaaa")
            if owner and (self._active_session_index is None or self._sessions[self._active_session_index].id != owner.id):
                text.append(f" · {owner.title}", style="#aaaaaa")
            status.update(text)
        else:
            status.update("")

    def action_copy_response(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.notify("Selected text copied")
            return
        session = self._ensure_active_session()
        message = next((m for m in reversed(session.messages) if m.role == "assistant" and m.content), None)
        if message:
            self.copy_to_clipboard(message.content)
            self.notify("Response copied")
        else:
            self.notify("No response to copy yet")

    def _show_transcript(self) -> None:
        # Model work can finish or timers fire while an approval/setup modal
        # owns the screen. Preserve data now; render after the modal closes.
        if isinstance(self.screen, ModalScreen):
            return
        executor_name = self.harness.executor.id.capitalize()
        transcript = []
        self._show_attachments()
        for index, message in enumerate(self._ensure_active_session().messages):
            label_style = (
                "bold #81aaff"
                if message.role == "user"
                else "bold #ffd166"
                if message.role == "terminal"
                else "bold #ff83c1"
            )
            label = "You" if message.role == "user" else "Terminal" if message.role == "terminal" else "Dennice"
            transcript.append(Text(label, style=label_style))
            if message.images:
                transcript.append(Text("Images: " + ", ".join(Path(path).name for path in message.images), style="#81aaff"))
            if message.role == "assistant" and not message.content and self._run_is_active:
                transcript.append(_activity_renderable(executor_name, self._activity_frame))
            elif message.role == "assistant":
                transcript.append(Markdown(message.content or "…"))
            else:
                transcript.append(Text(message.content or "…", style="#e7e7e7"))
            if index < len(self._conversation) - 1:
                transcript.append(Text(""))
        output = self.query_one("#output", Static)
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        follow = scroll.scroll_y >= scroll.max_scroll_y - 1
        output.update(Group(*transcript) if transcript else "")
        if follow:
            scroll.scroll_end(animate=False)

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
            f"permissions: {'none (chat only)' if executor.provider in API_PROVIDERS and not self.harness.config.tools.enabled else self._effective_permission_mode().value.replace('workspace-write', 'read-write')}\n"
            f"router: {self.harness.config.router.provider}; routing: {self.harness.config.routing.mode}"
        )

    def _status_text(self) -> str:
        directory = self._sessions[self._active_session_index].working_directory if self._active_session_index is not None else self._launch_directory
        branch = self._git_branches.get(str(directory), "")
        executor = self.harness.config.executor
        effort = executor.reasoning_effort.value if executor.reasoning_effort else "default"
        permission = ("none (chat only)" if executor.provider in API_PROVIDERS and not self.harness.config.tools.enabled
                      else self._effective_permission_mode().value.replace("workspace-write", "read-write"))
        context = self._context_meter()
        session = self._sessions[self._active_session_index] if self._active_session_index is not None else None
        same_route = bool(session and session.last_route_provider == executor.provider and
                          session.last_route_configured_model == executor.model and
                          session.last_route_effective_model)
        route_label = ""
        if same_route:
            routed_model = session.last_route_effective_model
            routed_effort = session.last_route_effective_effort or "default"
            if routed_model != executor.model or routed_effort != effort:
                active = self._run_is_active and self._running_session_id == session.id
                route_label = f"{'Run' if active else 'Last'} route: {routed_model}/{routed_effort}"
        run_usage = ""
        if session and session.last_run_budget_tokens is not None:
            active = self._run_is_active and self._running_session_id == session.id
            label = ("Goal" if active else "Last goal") if session.last_run_is_goal else ("Run" if active else "Last run")
            if session.last_run_tokens is None:
                state = "pending" if active else "unknown"
                run_usage = f"{label} usage {state}/{session.last_run_budget_tokens:,}"
            else:
                qualifier = "" if session.last_run_usage_complete else "≥"
                run_usage = f"{label} {qualifier}{session.last_run_tokens:,}/{session.last_run_budget_tokens:,} tokens"
        return (str(directory).replace(str(Path.home()), "~") + (f" · {branch}" if branch else "") +
                f" · Model: {executor.provider}/{executor.model} · Effort: {effort} · Permissions: {permission}"
                + (f" · {route_label}" if route_label else "")
                + (f" · {run_usage}" if run_usage else "") + f" · {context} · {__version__}")

    def _context_meter(self) -> str:
        session = self._ensure_active_session() if self._active_session_index is not None else None
        executor = self.harness.config.executor
        same_route = bool(session and session.last_route_provider == executor.provider and
                          session.last_route_configured_model == executor.model and
                          session.last_route_effective_model)
        context_model = session.last_route_effective_model if same_route else executor.model
        reported = bool(session and session.context_used_tokens is not None and
                        session.context_provider == executor.provider and
                        (same_route or executor.model in {"", "default"} or session.context_model == executor.model))
        if reported:
            used = session.context_used_tokens
            limit = session.context_window_tokens
            if not limit and context_model not in {"", "default"}:
                candidate = next((item for item in self.harness.config.routing.model_pool.get(executor.provider, [])
                                  if item.enabled and item.model == context_model), None)
                if candidate:
                    limit = candidate.context_tokens
        else:
            history = self._session_conversation_history(session) if session else []
            payload = "\n".join(item["content"] for item in history)
            prompt = ""
            if self.is_running and self.query("#workspace-task"):
                prompt = self.query_one("#workspace-task", TaskComposer).text
            elif self.query("#home-task"):
                prompt = self.query_one("#home-task", TaskComposer).text
            # Character-based estimate only; tokenizer, system prompt, tool schemas,
            # and image payloads are provider-specific and are not included.
            used = max(0, (len(payload) + len(prompt) + 3) // 4)
            limit = None
            if session and context_model not in {"", "default"}:
                candidate = next((item for item in self.harness.config.routing.model_pool.get(executor.provider, [])
                                  if item.enabled and item.model == context_model), None)
                if candidate:
                    limit = candidate.context_tokens
        prefix = "Ctx " if reported else "Ctx ~"
        if type(limit) is int and limit > 0:
            remaining = max(0, limit - used)
            return f"{prefix}{remaining:,} left ({used:,}/{limit:,})"
        return f"{prefix}{used:,} tokens · window limit unknown"

    def _refresh_statusline(self) -> None:
        if self.is_running and self.query("#statusline") and not isinstance(self.screen, ModalScreen):
            self.query_one("#statusline", Static).update(self._status_text())

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
