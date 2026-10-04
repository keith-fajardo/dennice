"""Staged, schema-validated extension configuration. Saving never runs code."""

import json

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from dennice.core.config import HookConfig, MCPServerConfig


def validate_extensions(kind, text):
    if len(text.encode()) > 64000:
        raise ValueError("Configuration is limited to 64 KB.")
    values = json.loads(text)
    if not isinstance(values, list) or len(values) > 50:
        raise ValueError("Enter a JSON array containing at most 50 entries.")
    model = HookConfig if kind == "hooks" else MCPServerConfig
    for value in values:
        if not isinstance(value, dict) or set(value) - set(model.model_fields):
            raise ValueError("Each entry must be an object with recognized configuration fields.")
    result = [model.model_validate(value) for value in values]
    if len({item.name for item in result}) != len(result):
        raise ValueError("Names must be unique within this configuration.")
    if kind == "mcp":
        import re
        for item in result:
            if item.transport == "stdio" and not item.command:
                raise ValueError("A stdio server needs a command argument array.")
            if item.transport == "http" and not item.url:
                raise ValueError("An HTTP server needs a URL.")
            names = [*item.env.keys(), *item.env.values()]
            if item.api_key_env:
                names.append(item.api_key_env)
            if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
                raise ValueError("Credentials and environment mappings accept variable names only, never secret values.")
    return result


class ExtensionConfigScreen(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True),
                Binding("ctrl+s", "stage", "Apply to setup", priority=True)]
    CSS = """
    ExtensionConfigScreen { align: center middle; background: #000000aa; }
    #extension-dialog { width: 90%; height: 85%; background: #161616; border: tall #f03c95; padding: 1 2; }
    #extension-description { height: auto; margin: 1 0; color: #bdc9df; }
    #extension-json { height: 1fr; background: #273346; border: solid #45638c; }
    #extension-error { height: auto; color: #ff8f98; margin: 1 0; }
    #extension-actions { height: 1; }
    #extension-actions Button { height: 1; min-height: 1; margin-right: 2; padding: 0 1; background: #375679; color: white; }
    """

    def __init__(self, kind, entries):
        super().__init__()
        self.kind, self.entries = kind, entries

    def compose(self) -> ComposeResult:
        with Vertical(id="extension-dialog"):
            yield Static(f"Configure {self.kind.upper()}")
            yield Static(
                "Edit the JSON array below. Ctrl+S stages validated changes; Save configuration in Setup persists them. "
                "This does not start processes, connect to servers, or grant trust. Changed commands/allowlists require new /hooks trust or /mcp trust. "
                "Use environment variable names for credentials, not keys.", id="extension-description")
            yield TextArea(json.dumps([entry.model_dump(mode="json") for entry in self.entries], indent=2), id="extension-json")
            yield Static("", id="extension-error")
            with Horizontal(id="extension-actions"):
                yield Button("Add disabled example", id="extension-example", compact=True)
                yield Button("Apply to setup", id="extension-apply", compact=True)
                yield Button("Cancel", id="extension-cancel", compact=True)

    def on_mount(self):
        self.query_one(TextArea).focus()

    def action_cancel(self):
        self.dismiss(None)

    def action_stage(self):
        try:
            result = validate_extensions(self.kind, self.query_one(TextArea).text)
        except (ValueError, TypeError):
            # Validation errors can echo supplied credential values; don't
            # expose those in notifications or diagnostic traces.
            self.query_one("#extension-error", Static).update(
                "Invalid configuration. Check JSON, unique names, recognized fields, argument arrays, timeouts and environment variable names.")
            return
        self.dismiss(result)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "extension-apply":
            self.action_stage()
        elif event.button.id == "extension-cancel":
            self.action_cancel()
        elif event.button.id == "extension-example":
            area = self.query_one(TextArea)
            try:
                values = json.loads(area.text)
                if not isinstance(values, list):
                    raise ValueError()
                names = {item.get("name") for item in values if isinstance(item, dict)}
                name, number = "example", 1
                while name in names:
                    number += 1
                    name = f"example_{number}"
                example = (HookConfig(name=name, event="before_tool", command=["python", "hook.py"])
                           if self.kind == "hooks" else MCPServerConfig(name=name, command=["python", "server.py"]))
                values.append(example.model_dump(mode="json"))
                area.load_text(json.dumps(values, indent=2))
            except (ValueError, TypeError):
                self.query_one("#extension-error", Static).update("Correct the JSON array before adding an example.")
