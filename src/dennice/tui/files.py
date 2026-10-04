"""Explicit user-operated, project-scoped file browser and editor.

This surface is not an agent tool and does not grant the agent write authority.
Secure descriptor-relative operations currently require POSIX support.
"""
from __future__ import annotations

import hashlib
import asyncio
import difflib
import os
import stat
import subprocess
import time
import uuid
from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import regex
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.message import Message
from textual import work
from textual.widgets import Button, Input, OptionList, RichLog, Static, TextArea, Tree


def replacement_preview(text: str, pattern: str, replacement: str) -> tuple[str, int]:
    """Bounded regex/template replacement; never touches disk."""
    if not pattern:
        return text, 0
    if len(pattern) > 256 or len(replacement) > 4096:
        raise FileSafetyError("Find pattern or replacement is too long.")
    expression = regex.compile(pattern)
    pieces, cursor, count, size = [], 0, 0, 0
    for match in expression.finditer(text, timeout=.025):
        count += 1
        if count > 4096:
            raise FileSafetyError("More than 4,096 matches; narrow the search.")
        before, changed = text[cursor:match.start()], match.expand(replacement)
        size += len(before.encode()) + len(changed.encode())
        if size > MAX_BYTES:
            raise FileSafetyError("Replacement exceeds the 512 KB file limit.")
        pieces.extend((before, changed))
        cursor = match.end()
    pieces.append(text[cursor:])
    result = "".join(pieces)
    if len(result.encode()) > MAX_BYTES:
        raise FileSafetyError("Replacement exceeds the 512 KB file limit.")
    return result, count

MAX_BYTES = 512_000
MAX_ENTRIES = 1_000
HIDDEN = {".git", ".dennice", ".aws", ".ssh", ".codex", ".claude", ".agents", ".venv", "node_modules", "__pycache__"}


class FileInput(Input):
    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False, priority=True)]


class FileSafetyError(ValueError):
    pass


def hidden_name(name: str) -> bool:
    lower = name.lower()
    return (
        lower in HIDDEN
        or lower.startswith(".env")
        or lower.endswith((".pem", ".key", ".p12", ".pfx"))
        or lower in {"secrets.env", "credentials", "credentials.json", "id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc"}
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    )


@dataclass(frozen=True)
class FileSnapshot:
    relative: str
    text: str
    digest: str
    signature: tuple[int, int, int, int, int]
    mode: int


@dataclass(frozen=True)
class FileMatch:
    relative: str
    line: int


@dataclass(frozen=True)
class GitChange:
    path: str
    index_status: str
    worktree_status: str

    @property
    def staged(self) -> bool:
        return self.index_status not in {" ", "?"}

    @property
    def changed(self) -> bool:
        return self.worktree_status != " " or self.index_status == "?"

    @property
    def label(self) -> str:
        marker = "" if self.index_status == "?" else self.index_status + self.worktree_status
        return f"{marker or '??'}  {self.path}"


@dataclass(frozen=True)
class FolderTarget:
    relative: str


class ExplorerTree(Tree):
    """Tree click handling uses the mouse-down row, avoiding terminal row drift."""
    BINDINGS = [Binding("shift+f10", "file_options", "File options", show=False, priority=True)]

    def __init__(self, *args, **kwargs):
        self.pointer_node = None
        super().__init__(*args, **kwargs)

    def action_file_options(self):
        # Keep the shortcut on the tree itself so it works when the explorer
        # has focus, even in terminals that consume right-click locally.
        self.app.query_one(WorkspaceFiles)._show_file_options(self.cursor_node)

    def _node_from_mouse(self, event):
        # Right-click events from several terminal protocols inherit stale
        # Rich style metadata from the preceding row. Their local coordinate
        # is reliable, so use it before metadata for context menus.
        if event.button == 3:
            pointer_line = int(event.offset.y + self.scroll_offset.y)
            node = self.get_node_at_line(pointer_line)
            if node is not None:
                return node
        meta = event.style.meta if event.style else {}
        node_id = meta.get("node") if meta else None
        if node_id is not None:
            try:
                return self.get_node_by_id(node_id)
            except Exception:
                pass
        line = meta.get("line") if meta else None
        if line is None and self.hover_line >= 0:
            line = self.hover_line
        if line is None:
            line = int(event.offset.y + self.scroll_offset.y)
        if line is None:
            return None
        node = self.get_node_at_line(line)
        if node is not None:
            return node
        # Some terminal mouse protocols (and Textual's Pilot) omit Rich's
        # rendered-line metadata for non-primary buttons. Fall back to the
        # stable TreeNode line indices so context-click still targets a row.
        pending = [self.root]
        while pending:
            candidate = pending.pop()
            if candidate.line == line:
                return candidate
            pending.extend(reversed(candidate.children))
        return None

    async def _on_mouse_down(self, event):
        self.pointer_node = self._node_from_mouse(event)
        await super()._on_mouse_down(event)

    async def _on_mouse_up(self, event):
        if event.button == 3:
            node = self.pointer_node or self._node_from_mouse(event)
            self.pointer_node = None
            if node is not None:
                self.move_cursor(node)
            self.app.query_one(WorkspaceFiles)._show_file_options(node)
            self.suppress_click()
            event.stop()
            return
        await super()._on_mouse_up(event)

    async def _on_click(self, event):
        node = self.pointer_node or self._node_from_mouse(event)
        # Some backends expose a context click only as Click (without the
        # matching MouseUp). Handle it here as well as in _on_mouse_up.
        if event.button == 3:
            self.pointer_node = None
            if node is not None:
                self.move_cursor(node)
            self.app.query_one(WorkspaceFiles)._show_file_options(node)
            event.stop()
            return
        if node is None:
            return
        self.move_cursor(node)
        if node.allow_expand:
            # Expand/collapse folders (and inline match groups) with a single
            # click. A double-click is two click events; only the first should
            # toggle, otherwise the tree immediately returns to its old state.
            if event.chain == 1:
                self._toggle_node(node)
        else:
            await self.run_action("select_cursor")


class ProjectFiles:
    def __init__(self, root: Path):
        if root.is_symlink():
            raise FileSafetyError("The project root may not be a symbolic link.")
        self.root = root.resolve(strict=True)

    def parts(self, relative: str) -> tuple[str, ...]:
        path = Path(relative)
        parts = path.parts
        if path.is_absolute() or not parts or any(p in {".", ".."} or hidden_name(p) for p in parts):
            raise FileSafetyError("Path is outside the project or is protected.")
        return parts

    @contextmanager
    def parent(self, relative: str):
        parts = self.parts(relative)
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise FileSafetyError("Secure file access is unavailable on this platform. Use an external editor (or WSL); no file was opened or changed.")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor, parts[-1]
        except OSError as error:
            raise FileSafetyError("Cannot securely access this project file; symlinks and inaccessible paths are rejected.") from error
        finally:
            os.close(descriptor)

    @contextmanager
    def directory(self, parts: tuple[str, ...]):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise FileSafetyError("Secure file operations are unavailable on this platform.")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        except OSError as error:
            raise FileSafetyError("Cannot securely access the destination folder; symlinks are rejected.") from error
        finally:
            os.close(descriptor)

    def read(self, relative: str) -> FileSnapshot:
        with self.parent(relative) as (parent, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                    raise FileSafetyError("Only regular, non-linked text files up to 512 KB can be opened.")
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    data = stream.read(MAX_BYTES + 1)
                after = os.fstat(descriptor)
                if len(data) > MAX_BYTES or self.signature(info) != self.signature(after):
                    raise FileSafetyError("The file is too large or changed while being read.")
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise FileSafetyError("Binary or non-UTF-8 files cannot be edited.") from error
                if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                    raise FileSafetyError("Binary/control-character files cannot be edited.")
                return FileSnapshot(relative, text, hashlib.sha256(data).hexdigest(), self.signature(info), stat.S_IMODE(info.st_mode))
            finally:
                os.close(descriptor)

    @staticmethod
    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def save(self, snapshot: FileSnapshot, text: str) -> FileSnapshot:
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise FileSafetyError("Edited file exceeds the 512 KB limit.")
        with self.parent(snapshot.relative) as (parent, name):
            current = self.read(snapshot.relative)
            if current.signature != snapshot.signature or current.digest != snapshot.digest:
                raise FileSafetyError("File changed outside the editor. Save refused; close and reopen to review it.")
            temporary = f".dennice-edit-{uuid.uuid4().hex}"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, snapshot.mode, dir_fd=parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fchmod(stream.fileno(), snapshot.mode)
                    os.fsync(stream.fileno())
                current = self.read(snapshot.relative)
                if current.signature != snapshot.signature or current.digest != snapshot.digest:
                    raise FileSafetyError("File changed outside the editor. Save refused.")
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return self.read(snapshot.relative)

    def create(self, relative: str, text: str = "") -> FileSnapshot:
        """Create a new empty file without following links or replacing anything."""
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise FileSafetyError("File exceeds the 512 KB limit.")
        with self.parent(relative) as (parent, name):
            descriptor = None
            try:
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
                with os.fdopen(os.dup(descriptor), "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(descriptor)
                os.fsync(parent)
            except FileExistsError as error:
                raise FileSafetyError("A file with that name already exists; nothing was overwritten.") from error
            except OSError as error:
                raise FileSafetyError("Could not safely create the file in this folder.") from error
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        return self.read(relative)

    def move_file(self, relative: str, destination: str, new_name: str | None = None) -> str:
        """Move a regular file within this root; hard-link creation is no-overwrite."""
        source_parts = self.parts(relative)
        destination_dir_parts = self.parts(destination) if destination else ()
        name = new_name or source_parts[-1]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise FileSafetyError("Enter a single filename without a path.")
        target_relative = str(Path(destination, name)) if destination else name
        target_parts = self.parts(target_relative)
        if source_parts == target_parts:
            raise FileSafetyError("Choose a different destination folder.")
        with self.parent(relative) as (source_parent, source_name):
            with self.directory(destination_dir_parts) as target_parent:
                try:
                    info = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise FileSafetyError("Only regular, non-linked files can be moved.")
                    os.link(source_name, target_parts[-1], src_dir_fd=source_parent,
                            dst_dir_fd=target_parent, follow_symlinks=False)
                except FileExistsError as error:
                    raise FileSafetyError("A file with that name already exists in the destination; nothing was overwritten.") from error
                except FileSafetyError:
                    raise
                except OSError as error:
                    raise FileSafetyError("Could not safely move this file.") from error
                source_removed = False
                try:
                    linked = os.stat(target_parts[-1], dir_fd=target_parent, follow_symlinks=False)
                    current = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
                    if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                        raise FileSafetyError("The file changed during the move; the source was left untouched.")
                    os.fsync(target_parent)
                    os.unlink(source_name, dir_fd=source_parent)
                    source_removed = True
                    os.fsync(source_parent)
                except (OSError, FileSafetyError) as error:
                    if not source_removed:
                        try:
                            linked = os.stat(target_parts[-1], dir_fd=target_parent, follow_symlinks=False)
                            if (linked.st_dev, linked.st_ino) == (info.st_dev, info.st_ino):
                                os.unlink(target_parts[-1], dir_fd=target_parent)
                        except OSError:
                            pass
                    if isinstance(error, FileSafetyError):
                        raise
                    message = "File moved, but folder sync failed." if source_removed else "Move did not complete; check both folders before retrying."
                    raise FileSafetyError(message) from error
        return target_relative

    def duplicate_file(self, relative: str, new_name: str) -> FileSnapshot:
        if Path(new_name).name != new_name or "/" in new_name or "\\" in new_name:
            raise FileSafetyError("Enter a single filename without a path.")
        parent = str(Path(relative).parent)
        target = str(Path(parent, new_name)) if parent != "." else new_name
        snapshot = self.read(relative)
        return self.create(target, snapshot.text)

    def delete_file(self, relative: str) -> None:
        """Delete a regular file only (never a directory or symlink)."""
        with self.parent(relative) as (parent, name):
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise FileSafetyError("Only regular, non-linked files can be deleted here.")
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            except FileSafetyError:
                raise
            except OSError as error:
                raise FileSafetyError("Could not safely delete this file.") from error

    def entries(self) -> tuple[list[tuple[str, bool]], bool]:
        result = []
        truncated = False
        def scan(descriptor: int, prefix: tuple[str, ...], depth: int):
            nonlocal truncated
            if depth > 12 or len(result) >= MAX_ENTRIES:
                truncated = True
                return
            try:
                with os.scandir(descriptor) as iterator:
                    entries = []
                    for entry in iterator:
                        if len(entries) >= MAX_ENTRIES:
                            truncated = True
                            break
                        if not hidden_name(entry.name) and not entry.is_symlink():
                            entries.append(entry)
                for entry in sorted(entries, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
                    if len(result) >= MAX_ENTRIES:
                        truncated = True
                        break
                    is_dir = entry.is_dir(follow_symlinks=False)
                    relative = str(Path(*prefix, entry.name))
                    result.append((relative, is_dir))
                    if is_dir:
                        try:
                            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                        except OSError:
                            continue
                        try:
                            scan(child, (*prefix, entry.name), depth + 1)
                        finally:
                            os.close(child)
            except OSError:
                return
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise FileSafetyError("Secure file browsing requires POSIX/WSL; use an external editor on Windows.")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            scan(descriptor, (), 0)
        finally:
            os.close(descriptor)
        return result, truncated


class DiscardEditsScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "keep", priority=True)]
    DEFAULT_CSS = "DiscardEditsScreen { align: center middle; } #discard-dialog { width: 54; height: auto; padding: 1 2; border: solid $warning; background: $surface; } #discard-dialog Horizontal { height: 1; margin-top: 1; } #discard-dialog Button { height: 1; min-height: 1; border: none; padding: 0 1; margin-right: 2; }"
    def compose(self) -> ComposeResult:
        with Vertical(id="discard-dialog"):
            yield Static("Unsaved changes. Discard them and close?")
            with Horizontal():
                yield Button("Keep editing", id="keep-edits", variant="primary", compact=True)
                yield Button("Discard", id="discard-edits", variant="error", compact=True)
    def on_mount(self):
        self.query_one("#keep-edits", Button).focus()
    def action_keep(self):
        self.dismiss(False)
    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "discard-edits")


class FileEditorScreen(ModalScreen[None]):
    BINDINGS = [Binding("ctrl+shift+s", "save_file", "Save", priority=True), Binding("ctrl+z", "undo_edit", "Undo", priority=True), Binding("ctrl+shift+z", "redo_edit", "Redo", priority=True), Binding("ctrl+h", "find_replace", "Find/replace", priority=True), Binding("escape", "close_editor", "Close", priority=True)]
    DEFAULT_CSS = "FileEditorScreen { align: center middle; } #file-editor-dialog { width: 90%; height: 90%; padding: 1; border: solid $accent; background: $surface; } #file-editor { height: 1fr; } #file-editor-status { height: auto; } #file-editor-dialog Input { height: 1; min-height: 1; border: none; padding: 0 1; background: #273346; color: #f1f5ff; margin-top: 1; } #file-editor-dialog Input:focus { background: #304f75; } #file-editor-dialog Button { height: 1; min-height: 1; border: none; padding: 0 1; background: #375679; color: #f1f5ff; margin: 1 0; } #file-replace-status { height: 1; }"
    def __init__(self, files: ProjectFiles, snapshot: FileSnapshot, line: int | None = None):
        super().__init__()
        self.files, self.snapshot = files, snapshot
        self.line = line
    def compose(self) -> ComposeResult:
        with Vertical(id="file-editor-dialog"):
            yield Static(self.snapshot.relative, markup=False)
            yield Static("Explicit user editor · Ctrl+Shift+S save · Ctrl+Z undo · Ctrl+Shift+Z redo · Esc close", markup=False)
            yield Static("Find (regex) · Ctrl+H", markup=False)
            yield FileInput(placeholder="Find in file", id="editor-find")
            yield Static(r"Replace with · backreferences: \1", markup=False)
            yield FileInput(placeholder="Replacement text", id="editor-replace")
            yield Static("", id="file-replace-status", markup=False)
            yield Button("Replace all in buffer · save explicitly", id="editor-replace-all", compact=True)
            yield TextArea(self.snapshot.text, id="file-editor", soft_wrap=False)
            yield Static("", id="file-editor-status", markup=False)
    def on_mount(self):
        editor = self.query_one("#file-editor", TextArea)
        editor.focus()
        if self.line:
            editor.move_cursor((min(self.line - 1, len(self.snapshot.text.split("\n")) - 1), 0), center=True)
    def action_find_replace(self):
        self.query_one("#editor-find", Input).focus()
    def _replacement(self):
        return replacement_preview(self.query_one("#file-editor", TextArea).text,
            self.query_one("#editor-find", Input).value, self.query_one("#editor-replace", Input).value)
    def on_input_changed(self, event: Input.Changed):
        try:
            _text, count = self._replacement()
            status = f"{count} matches · preview only; disk unchanged"
        except (regex.error, TimeoutError, FileSafetyError, IndexError) as error:
            status = f"Find/replace: {error}"
        self.query_one("#file-replace-status", Static).update(status)
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id != "editor-replace-all":
            return
        event.stop()
        try:
            text, count = self._replacement()
            editor = self.query_one("#file-editor", TextArea)
            if count:
                lines = editor.text.split("\n")
                editor.replace(text, (0, 0), (len(lines) - 1, len(lines[-1])), maintain_selection_offset=False)
            self.query_one("#file-replace-status", Static).update(f"{count} replacements in buffer · Ctrl+Z undo · Ctrl+Shift+S save")
        except (regex.error, TimeoutError, FileSafetyError, IndexError) as error:
            self.query_one("#file-replace-status", Static).update(f"Find/replace: {error}")
    def action_save_file(self):
        try:
            self.snapshot = self.files.save(self.snapshot, self.query_one("#file-editor", TextArea).text)
            status = "Saved."
        except (FileSafetyError, OSError) as error:
            status = f"Save failed: {error}"
        self.query_one("#file-editor-status", Static).update(status)
    def action_undo_edit(self):
        self.query_one("#file-editor", TextArea).action_undo()
    def action_redo_edit(self):
        self.query_one("#file-editor", TextArea).action_redo()
    def action_close_editor(self):
        if self.query_one("#file-editor", TextArea).text != self.snapshot.text:
            self.app.push_screen(DiscardEditsScreen(), self._discard_result)
        else:
            self.dismiss(None)
    def _discard_result(self, discard: bool):
        if discard:
            self.dismiss(None)


class FilePreviewScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close_preview", "Close", priority=True)]
    DEFAULT_CSS = "FilePreviewScreen { align: center middle; } #file-preview-dialog { width: 90%; height: 90%; padding: 1; border: solid $accent; background: $surface; } #file-preview-scroll { height: 1fr; }"
    def __init__(self, snapshot: FileSnapshot, line: int | None = None):
        super().__init__()
        self.snapshot = snapshot
        self.line = line
    def compose(self) -> ComposeResult:
        with Vertical(id="file-preview-dialog"):
            yield Static(f"{self.snapshot.relative} · read only · Esc close", markup=False)
            if self.line:
                lines = self.snapshot.text.splitlines()
                yield Static(f"Matching line {self.line}: {lines[self.line - 1] if self.line <= len(lines) else ''}", markup=False)
            with VerticalScroll(id="file-preview-scroll"):
                yield Static(self.snapshot.text, markup=False)
    def action_close_preview(self):
        self.dismiss(None)


class FilePreviewPane(Vertical):
    """Embedded file viewer/editor with bounded find and staged replacement."""
    BINDINGS = [Binding("ctrl+f", "find", "Find in file", priority=True),
                Binding("ctrl+h", "find", "Find/replace", priority=True),
                Binding("ctrl+shift+s", "save_file", "Save", priority=True),
                Binding("ctrl+z", "undo_edit", "Undo", priority=True),
                Binding("ctrl+shift+z", "redo_edit", "Redo", priority=True),
                Binding("escape", "close", "Back to chat", priority=True),
                Binding("f3", "match(1)", "Next match"),
                Binding("shift+f3", "match(-1)", "Previous match")]
    DEFAULT_CSS = """
    FilePreviewPane { display: none; height: 1fr; padding: 0 1; background: #141c28; }
    #preview-title { height: auto; max-height: 3; color: #bdc9df; margin-bottom: 1; }
    #preview-find { height: 1; min-height: 1; border: none; padding: 0 1; background: #273346; color: white; }
    #preview-find:focus { background: #304f75; }
    #preview-replace { height: 1; min-height: 1; border: none; padding: 0 1; background: #273346; color: white; }
    #preview-replace:focus { background: #304f75; }
    #preview-actions, #preview-edit-actions { height: 1; margin: 1 0; }
    #preview-actions Button, #preview-edit-actions Button { height: 1; min-height: 1; border: none; padding: 0 1; margin-right: 1; background: #375679; color: white; }
    #preview-find-status { height: 1; color: #bdc9df; }
    #preview-replace-row { height: 1; margin-bottom: 1; }
    #preview-replace { width: 1fr; min-width: 8; }
    #preview-replace-one { width: 13; min-width: 13; }
    #preview-replace-all { width: 23; min-width: 23; }
    #preview-replace-status { height: 1; color: #bdc9df; }
    #preview-content { height: 1fr; background: #101722; }
    """
    class Closed(Message):
        pass

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._loaded_key: tuple[str, bool, str, str] | None = None
        self.matches, self.match_index, self.partial = [], 0, False
        self.line_offsets = [0]
        self.files = None
        self.snapshot = None
        self.edit_mode = False

    def compose(self):
        yield Static("", id="preview-title", markup=False)
        yield FileInput(placeholder="Find in this file (regex) · Ctrl+F", id="preview-find")
        with Horizontal(id="preview-replace-row"):
            yield FileInput(placeholder=r"Replace with (backreferences: \1)", id="preview-replace")
            yield Button("Replace", id="preview-replace-one", compact=True)
            yield Button("Replace all in buffer", id="preview-replace-all", compact=True)
        yield Static("", id="preview-replace-status", markup=False)
        with Horizontal(id="preview-actions"):
            yield Button("Previous", id="preview-previous", compact=True)
            yield Button("Next", id="preview-next", compact=True)
            yield Button("Edit", id="preview-toggle-edit", compact=True)
            yield Button("Close", id="preview-close", compact=True)
        with Horizontal(id="preview-edit-actions"):
            yield Button("Undo", id="preview-undo", compact=True)
            yield Button("Redo", id="preview-redo", compact=True)
            yield Button("Save", id="preview-save", compact=True, variant="primary")
        yield Static("", id="preview-find-status", markup=False)
        yield TextArea(read_only=True, show_line_numbers=True, soft_wrap=False, id="preview-content")

    def open_file(self, snapshot, line=None, *, files=None, edit=False):
        self.display = True
        self.snapshot, self.files, self.edit_mode = snapshot, files, edit
        self.matches, self.match_index, self.partial = [], 0, False
        self.query_one("#preview-title", Static).update(f"{snapshot.relative} · {'edit' if edit else 'read only'} · Esc returns to chat")
        self.query_one("#preview-find", Input).value = ""
        self.query_one("#preview-replace", Input).value = ""
        self.query_one("#preview-replace-row").display = edit
        self.query_one("#preview-replace-one", Button).display = edit
        self.query_one("#preview-replace-all", Button).display = edit
        self.query_one("#preview-replace-status", Static).display = edit
        self.query_one("#preview-replace-status", Static).update("Replacement stays in the buffer until saved." if edit else "")
        self.query_one("#preview-toggle-edit", Button).label = "Open read-only" if edit else "Edit"
        for selector in ("#preview-undo", "#preview-redo", "#preview-save"):
            self.query_one(selector, Button).display = edit
        self.query_one("#preview-edit-actions").display = edit
        self.query_one("#preview-toggle-edit", Button).display = files is not None
        self.query_one("#preview-find-status", Static).update("Ctrl+F find · Enter/F3 next · Shift+F3 previous")
        content = self.query_one(TextArea)
        content.load_text(snapshot.text)
        content.read_only = not edit
        self.line_offsets = [0]
        for row in range(content.document.line_count - 1):
            self.line_offsets.append(self.line_offsets[-1] + len(content.document.get_line(row)) + len(content.document.newline))
        content.move_cursor((max(0, min((line or 1) - 1, content.document.line_count - 1)), 0), center=True)
        content.focus()

    def reset(self):
        self.display = False
        self.matches = []
        self.snapshot = self.files = None
        self.edit_mode = False
        self.line_offsets = [0]
        self.query_one(TextArea).load_text("")
        self.query_one("#preview-find", Input).value = ""
        self.query_one("#preview-replace", Input).value = ""

    def action_close(self):
        if self.has_unsaved_changes:
            self.app.push_screen(DiscardEditsScreen(), self._discard_result)
        else:
            self._finish_close()

    @property
    def has_unsaved_changes(self):
        return bool(self.edit_mode and self.snapshot and self.query_one("#preview-content", TextArea).text != self.snapshot.text)

    def _discard_result(self, discard):
        if discard:
            self._finish_close()

    def _finish_close(self):
        self.reset()
        self.post_message(self.Closed())

    def action_find(self):
        self.query_one("#preview-find", Input).focus()

    def action_save_file(self):
        if not self.edit_mode or not self.files or not self.snapshot:
            return
        try:
            self.snapshot = self.files.save(self.snapshot, self.query_one("#preview-content", TextArea).text)
            self.query_one("#preview-title", Static).update(f"{self.snapshot.relative} · edit · saved · Esc returns to chat")
            self.query_one("#preview-replace-status", Static).update("Saved to disk.")
        except (FileSafetyError, OSError) as error:
            self.query_one("#preview-replace-status", Static).update(f"Save failed: {error}")

    def action_undo_edit(self):
        if self.edit_mode:
            self.query_one("#preview-content", TextArea).action_undo()

    def action_redo_edit(self):
        if self.edit_mode:
            self.query_one("#preview-content", TextArea).action_redo()

    def _refresh_find(self):
        field = self.query_one("#preview-find", Input)
        self.matches, self.match_index, self.partial = [], 0, False
        content = self.query_one(TextArea)
        self.line_offsets = [0]
        for row in range(content.document.line_count - 1):
            self.line_offsets.append(self.line_offsets[-1] + len(content.document.get_line(row)) + len(content.document.newline))
        try:
            if len(field.value) > 256:
                raise FileSafetyError("Find is limited to 256 characters.")
            if field.value:
                expression = regex.compile(field.value, regex.IGNORECASE)
                for match in expression.finditer(content.text, timeout=.025):
                    if len(self.matches) == 4096:
                        self.partial = True
                        break
                    def location(index):
                        row = bisect_right(self.line_offsets, index) - 1
                        return row, min(index - self.line_offsets[row], len(content.document.get_line(row)))
                    self.matches.append((location(match.start()), location(match.end())))
            if self.matches:
                self.select_match()
            else:
                self.query_one("#preview-find-status", Static).update("No matches" if field.value else "Ctrl+F find · Enter/F3 next · Shift+F3 previous")
        except (regex.error, TimeoutError, FileSafetyError) as error:
            self.matches = []
            self.query_one("#preview-find-status", Static).update(f"Find: {error}")

    def _replace_preview(self):
        field = self.query_one("#preview-replace", Input)
        try:
            _text, count = replacement_preview(self.query_one(TextArea).text,
                self.query_one("#preview-find", Input).value, field.value)
            self.query_one("#preview-replace-status", Static).update(f"{count} matches · preview only; disk unchanged")
        except (regex.error, TimeoutError, FileSafetyError, IndexError) as error:
            self.query_one("#preview-replace-status", Static).update(f"Find/replace: {error}")

    def on_input_changed(self, event):
        if event.input.id not in {"preview-find", "preview-replace"} or event.value != event.input.value:
            return
        event.stop()
        if event.input.id == "preview-find":
            self._refresh_find()
        if self.edit_mode:
            self._replace_preview()

    def on_text_area_changed(self, event):
        if event.text_area.id != "preview-content" or not self.edit_mode:
            return
        self._refresh_find()
        self._replace_preview()

    def select_match(self):
        start, end = self.matches[self.match_index]
        content = self.query_one(TextArea)
        content.move_cursor(start)
        content.move_cursor(end, select=True, center=True)
        self.query_one("#preview-find-status", Static).update(
            f"{self.match_index + 1} / {len(self.matches)} matches · line {start[0] + 1}" +
            (" · partial (match limit)" if self.partial else ""))

    def action_match(self, direction):
        if self.matches:
            self.match_index = (self.match_index + direction) % len(self.matches)
            self.select_match()

    def on_input_submitted(self, event):
        if event.input.id == "preview-find":
            event.stop()
            self.action_match(1)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "preview-close":
            self.action_close()
        elif event.button.id == "preview-next":
            self.action_match(1)
        elif event.button.id == "preview-previous":
            self.action_match(-1)
        elif event.button.id == "preview-toggle-edit":
            if self.edit_mode:
                if self.has_unsaved_changes:
                    self.query_one("#preview-replace-status", Static).update("Save first or close and discard before switching to read-only.")
                    return
                self.edit_mode = False
                self.query_one(TextArea).read_only = True
                self.query_one("#preview-title", Static).update(f"{self.snapshot.relative} · read only · Esc returns to chat")
                self.query_one("#preview-toggle-edit", Button).label = "Edit"
                self.query_one("#preview-replace-row").display = False
                self.query_one("#preview-replace-one", Button).display = False
                self.query_one("#preview-replace-all", Button).display = False
                self.query_one("#preview-replace-status", Static).display = False
                for selector in ("#preview-undo", "#preview-redo", "#preview-save"):
                    self.query_one(selector, Button).display = False
                self.query_one("#preview-edit-actions").display = False
            else:
                self.edit_mode = True
                self.query_one(TextArea).read_only = False
                self.query_one("#preview-title", Static).update(f"{self.snapshot.relative} · edit · Esc returns to chat")
                self.query_one("#preview-toggle-edit", Button).label = "Open read-only"
                self.query_one("#preview-replace-row").display = True
                self.query_one("#preview-replace-one", Button).display = True
                self.query_one("#preview-replace-all", Button).display = True
                self.query_one("#preview-replace-status", Static).display = True
                for selector in ("#preview-undo", "#preview-redo", "#preview-save"):
                    self.query_one(selector, Button).display = True
                self.query_one("#preview-edit-actions").display = True
        elif event.button.id == "preview-save":
            self.action_save_file()
        elif event.button.id == "preview-undo":
            self.action_undo_edit()
        elif event.button.id == "preview-redo":
            self.action_redo_edit()
        elif event.button.id == "preview-replace-one":
            if not self.edit_mode:
                return
            try:
                pattern = self.query_one("#preview-find", Input).value
                replacement = self.query_one("#preview-replace", Input).value
                if not pattern or not self.matches:
                    self.query_one("#preview-replace-status", Static).update("No current match to replace.")
                    return
                text = self.query_one(TextArea).text
                expression = regex.compile(pattern, regex.IGNORECASE)
                match = next(item for index, item in enumerate(expression.finditer(text, timeout=.025))
                             if index == self.match_index)
                changed = text[:match.start()] + match.expand(replacement) + text[match.end():]
                editor = self.query_one(TextArea)
                lines = text.split("\n")
                editor.replace(changed, (0, 0), (len(lines) - 1, len(lines[-1])), maintain_selection_offset=False)
                self.query_one("#preview-replace-status", Static).update("1 replacement staged in buffer · save explicitly")
                self._refresh_find()
            except (regex.error, TimeoutError, FileSafetyError, IndexError, StopIteration) as error:
                self.query_one("#preview-replace-status", Static).update(f"Replace: {error}")
        elif event.button.id == "preview-replace-all":
            if not self.edit_mode:
                return
            try:
                text, count = replacement_preview(self.query_one(TextArea).text,
                    self.query_one("#preview-find", Input).value,
                    self.query_one("#preview-replace", Input).value)
                editor = self.query_one(TextArea)
                if count:
                    lines = editor.text.split("\n")
                    editor.replace(text, (0, 0), (len(lines) - 1, len(lines[-1])), maintain_selection_offset=False)
                self.query_one("#preview-replace-status", Static).update(f"{count} replacements in buffer · Ctrl+Z undo · Ctrl+Shift+S save")
                self._refresh_find()
            except (regex.error, TimeoutError, FileSafetyError, IndexError) as error:
                self.query_one("#preview-replace-status", Static).update(f"Find/replace: {error}")


class GitDiffPane(Vertical):
    """Read-only, aligned working-tree/index diff displayed beside its base."""
    DEFAULT_CSS = """
    GitDiffPane { display: none; height: 1fr; padding: 0 1; background: #141c28; }
    #git-diff-title { height: 1; color: #bdc9df; }
    #git-diff-columns { height: 1fr; }
    #git-diff-columns Vertical { width: 1fr; height: 1fr; }
    #git-diff-columns Static { height: 1; color: #a9bbd2; }
    #git-diff-columns RichLog { width: 1fr; min-width: 0; height: 1fr; background: #101722; padding: 0 1; }
    #git-diff-close { width: 12; height: 1; min-height: 1; border: none; padding: 0 1; margin: 1 0; background: #375679; color: white; }
    """
    class Closed(Message):
        pass

    def compose(self):
        yield Static("", id="git-diff-title", markup=False)
        with Horizontal(id="git-diff-columns"):
            with Vertical():
                yield Static("Base (− removed)", id="git-diff-base-label")
                yield RichLog(wrap=False, highlight=False, markup=False, auto_scroll=False, id="git-diff-base")
            with Vertical():
                yield Static("Working tree (+ added)", id="git-diff-current-label")
                yield RichLog(wrap=False, highlight=False, markup=False, auto_scroll=False, id="git-diff-current")
        yield Button("Close", id="git-diff-close", compact=True)

    def open_diff(self, files: ProjectFiles, entry: GitChange, *, staged: bool):
        """Load a bounded text comparison; staged compares HEAD→index, changes index→worktree."""
        key = (entry.path, staged, entry.index_status, entry.worktree_status)
        # Option-list selection can be emitted more than once while a user is
        # navigating. Do not clear and re-write an already open diff: RichLog
        # would reset its viewport to row zero each time.
        if self.display and self._loaded_key == key:
            return
        if hidden_name(Path(entry.path).name):
            raise FileSafetyError("Protected files cannot be previewed in Git Changes.")
        files.parts(entry.path)
        if staged:
            base = self._git_blob(files.root, f"HEAD:{entry.path}", optional=True)
            current = self._git_blob(files.root, f":{entry.path}", optional=True)
            left_label, right_label = "HEAD", "Staged"
        else:
            base = self._git_blob(files.root, f":{entry.path}", optional=True)
            if base is None:
                base = ""
            try:
                current = files.read(entry.path).text
            except FileNotFoundError:
                current = ""
            left_label, right_label = "Index", "Working tree"
        base = base or ""
        current = current or ""
        old_lines, new_lines = self._align(base.splitlines(), current.splitlines())
        self.query_one("#git-diff-title", Static).update(f"{entry.path} · {entry.label.strip()} · {'staged' if staged else 'working changes'}")
        self.query_one("#git-diff-base-label", Static).update(left_label)
        self.query_one("#git-diff-current-label", Static).update(right_label)
        self._write_diff(self.query_one("#git-diff-base", RichLog), old_lines)
        self._write_diff(self.query_one("#git-diff-current", RichLog), new_lines)
        self._loaded_key = key
        self.display = True

    @staticmethod
    def _write_diff(log: RichLog, lines: list[str]) -> None:
        log.clear()
        for number, line in enumerate(lines, start=1):
            if line.startswith("− "):
                style = "#ffc1c1 on #44232b"
            elif line.startswith("+ "):
                style = "#b8f2c2 on #1c3b2a"
            else:
                style = "#c5ceda"
            rendered = Text(f"{number:4}  ", style="#8290a3")
            rendered.append(line, style=style)
            # Explicitly disable RichLog's write-time scroll behavior. The
            # comparison remains exactly where the user leaves it.
            log.write(rendered, scroll_end=False)

    @staticmethod
    def _git_blob(root: Path, spec: str, *, optional: bool) -> str | None:
        try:
            result = subprocess.run(["git", "show", spec], cwd=root, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=2, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FileSafetyError(f"Could not read the Git version: {error}") from error
        if result.returncode:
            if optional:
                return None
            raise FileSafetyError("Git could not read the selected file version.")
        if len(result.stdout) > MAX_BYTES:
            raise FileSafetyError("Git diff preview is limited to 512 KB per version.")
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise FileSafetyError("Binary files cannot be shown in the text diff.") from error

    @staticmethod
    def _align(left: list[str], right: list[str]) -> tuple[list[str], list[str]]:
        aligned_left, aligned_right = [], []
        for operation, a0, a1, b0, b1 in difflib.SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes():
            if operation == "equal":
                aligned_left.extend(f"  {line}" for line in left[a0:a1]); aligned_right.extend(f"  {line}" for line in right[b0:b1])
            elif operation == "delete":
                aligned_left.extend(f"− {line}" for line in left[a0:a1]); aligned_right.extend([""] * (a1 - a0))
            elif operation == "insert":
                aligned_left.extend([""] * (b1 - b0)); aligned_right.extend(f"+ {line}" for line in right[b0:b1])
            else:
                count = max(a1 - a0, b1 - b0)
                aligned_left.extend([f"− {line}" for line in left[a0:a1]] + [""] * (count - (a1 - a0)))
                aligned_right.extend([f"+ {line}" for line in right[b0:b1]] + [""] * (count - (b1 - b0)))
        return aligned_left, aligned_right

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "git-diff-close":
            event.stop()
            self.display = False
            self.post_message(self.Closed())


class FileActionScreen(ModalScreen[str | None]):
    """Compatibility screen retained for older integrations; the explorer uses inline options."""
    BINDINGS = [Binding("escape", "close_actions", priority=True)]
    DEFAULT_CSS = "FileActionScreen { align: center middle; } #file-actions-dialog { width: 58; height: auto; padding: 1 2; border: solid $accent; background: $surface; } #file-actions-dialog Button { height: 1; min-height: 1; border: none; padding: 0 1; margin-top: 1; background: #375679; color: #f1f5ff; }"
    def __init__(self, relative: str):
        super().__init__()
        self.relative = relative
    def compose(self) -> ComposeResult:
        with Vertical(id="file-actions-dialog"):
            yield Static(self.relative, markup=False)
            yield Button("Open read-only", id="file-show", variant="primary", compact=True)
            yield Button("Edit", id="file-edit", compact=True)
            yield Button("Cancel", id="file-action-cancel", compact=True)
    def on_mount(self):
        self.query_one("#file-show", Button).focus()
    def action_close_actions(self):
        self.dismiss(None)
    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss({"file-show": "preview", "file-edit": "edit"}.get(event.button.id))


class DirectoryPicker(ModalScreen[Path | None]):
    """User-operated folder picker; lists immediate directories, never files."""
    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]
    DEFAULT_CSS = """
    DirectoryPicker { align: center middle; background: #000000aa; }
    #directory-dialog { width: 76; max-width: 95%; height: 75%; background: #161616; border: solid #668ac1; padding: 1 2; }
    #directory-path { height: 1; min-height: 1; border: none; background: #273346; margin: 1 0; }
    #directory-list { height: 1fr; background: #141c28; }
    #directory-status { height: auto; margin: 1 0; }
    #directory-buttons { height: 1; }
    #directory-buttons Button { height: 1; min-height: 1; margin-right: 2; border: none; padding: 0 1; background: #375679; color: white; }
    """
    def __init__(self, root):
        super().__init__()
        self.directory = Path(root)
        self.choices = []
        self.valid = False

    def compose(self):
        with Vertical(id="directory-dialog"):
            yield Static("Open folder · change this session's working directory")
            yield FileInput(value=str(self.directory), id="directory-path")
            yield OptionList(id="directory-list")
            yield Static("↑/↓ select · Enter browse · Open folder confirms", id="directory-status", markup=False)
            with Horizontal(id="directory-buttons"):
                yield Button("Open folder", id="directory-confirm", compact=True)
                yield Button("Cancel", id="directory-cancel", compact=True)

    def on_mount(self):
        self.browse(self.directory)
        self.query_one(OptionList).focus()

    def browse(self, path):
        listing = self.query_one(OptionList)
        listing.clear_options()
        self.choices = []
        self.valid = False
        self.query_one("#directory-confirm", Button).disabled = True
        try:
            directory = Path(path).expanduser()
            if not directory.is_absolute():
                directory = self.directory / directory
            directory = directory.resolve(strict=True)
            if not directory.is_dir():
                raise ValueError("Choose a directory, not a file.")
            choices = []
            truncated = False
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= 2000 or len(choices) >= 200:
                        truncated = True
                        break
                    if entry.is_dir(follow_symlinks=False):
                        choices.append(directory / entry.name)
            self.directory = directory
            self.choices = ([directory.parent] if directory.parent != directory else []) + sorted(choices, key=lambda item: item.name.casefold())
            listing.add_options([Text(".. / parent" if item == directory.parent else item.name + "/") for item in self.choices])
            self.query_one("#directory-path", Input).value = str(directory)
            self.query_one("#directory-status", Static).update(
                "↑/↓ select · Enter browse · Open folder confirms" + (" · partial list; enter a path to browse more" if truncated else ""))
            self.valid = True
            self.query_one("#directory-confirm", Button).disabled = False
        except (OSError, ValueError) as error:
            self.query_one("#directory-status", Static).update(f"Cannot open folder: {error}")

    def on_input_submitted(self, event):
        event.stop()
        self.browse(event.value)
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event):
        event.stop()
        if event.option_index < len(self.choices):
            self.browse(self.choices[event.option_index])

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "directory-confirm" and self.valid:
            # Revalidate at confirmation; editing the path requires Enter first.
            self.browse(self.query_one("#directory-path", Input).value)
            if self.valid:
                self.dismiss(self.directory)
        elif event.button.id == "directory-cancel":
            self.dismiss(None)


class NewFileScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]
    DEFAULT_CSS = "NewFileScreen { align: center middle; background: #000000aa; } #new-file-dialog { width: 60; height: auto; padding: 1 2; border: solid #668ac1; background: #161616; } #new-file-name { height: 1; min-height: 1; border: none; padding: 0 1; margin-top: 1; background: #273346; color: #f1f5ff; } #new-file-buttons { height: 1; margin-top: 1; } #new-file-buttons Button { height: 1; min-height: 1; margin-right: 2; border: none; padding: 0 1; background: #375679; color: white; }"
    def __init__(self, directory: str, *, heading="Create an empty file in:", initial="", action="Create file"):
        super().__init__()
        self.directory, self.heading, self.initial, self.action_label = directory, heading, initial, action
    def compose(self):
        with Vertical(id="new-file-dialog"):
            yield Static(f"{self.heading} {self.directory or '.'}", markup=False)
            yield FileInput(value=self.initial, placeholder="File name", id="new-file-name")
            with Horizontal(id="new-file-buttons"):
                yield Button(self.action_label, id="new-file-create", compact=True, variant="primary")
                yield Button("Cancel", id="new-file-cancel", compact=True)
    def on_mount(self):
        self.query_one("#new-file-name", Input).focus()
    def action_cancel(self):
        self.dismiss(None)
    def on_input_submitted(self, event):
        event.stop()
        self._create()
    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "new-file-create":
            self._create()
        elif event.button.id == "new-file-cancel":
            self.dismiss(None)
    def _create(self):
        name = self.query_one("#new-file-name", Input).value.strip()
        if not name:
            self.notify("Enter a file name.", severity="warning")
            return
        if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            self.notify("Enter a single file name, without a folder path.", severity="error")
            return
        self.dismiss(name)


class FileOperationConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]
    DEFAULT_CSS = "FileOperationConfirmScreen { align: center middle; background: #000000aa; } #file-operation-confirm { width: 68; height: auto; padding: 1 2; border: solid #d9a441; background: #161616; } #file-operation-buttons { height: 1; margin-top: 1; } #file-operation-buttons Button { height: 1; min-height: 1; margin-right: 2; border: none; padding: 0 1; background: #375679; color: white; }"
    def __init__(self, heading: str, detail: str, *, destructive=False, affirmative=None):
        super().__init__()
        self.heading, self.detail, self.destructive = heading, detail, destructive
        self.affirmative = affirmative or ("Delete" if destructive else "Move file")
    def compose(self):
        with Vertical(id="file-operation-confirm"):
            yield Static(self.heading, markup=False)
            yield Static(self.detail, markup=False)
            if self.destructive:
                yield Static("This permanently deletes the file; this action cannot be undone.", markup=False)
            with Horizontal(id="file-operation-buttons"):
                yield Button(self.affirmative, id="file-operation-confirm-yes", compact=True, variant="error" if self.destructive else "primary")
                yield Button("Cancel", id="file-operation-confirm-no", compact=True)
    def on_mount(self):
        self.query_one("#file-operation-confirm-no", Button).focus()
    def action_cancel(self):
        self.dismiss(False)
    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == "file-operation-confirm-yes")


class WorkspaceReplaceScreen(ModalScreen[None]):
    """Live content matches; replacements are staged one file at a time."""
    BINDINGS = [Binding("escape", "close_search", "Close", priority=True)]
    DEFAULT_CSS = "WorkspaceReplaceScreen { align: center middle; } #workspace-replace-dialog { width: 80%; height: 80%; padding: 1 2; border: solid $accent; background: $surface; } #workspace-replace-dialog Input { height: 1; min-height: 1; border: none; padding: 0 1; margin-top: 1; background: #273346; color: #f1f5ff; } #workspace-replace-dialog Input:focus { background: #304f75; } #workspace-replace-dialog Button { height: 1; min-height: 1; border: none; padding: 0 1; margin-top: 1; background: #375679; color: #f1f5ff; } #replace-results { height: 1fr; } #replace-status { height: auto; margin: 1 0; }"
    def __init__(self, files: ProjectFiles, *, session_id=None, pattern="", replacement="", replace_enabled=True):
        super().__init__()
        self.files, self.results = files, []
        self.session_id, self.pattern, self.replacement = session_id, pattern, replacement
        self.replace_enabled = replace_enabled
    def compose(self) -> ComposeResult:
        with Vertical(id="workspace-replace-dialog"):
            yield Static("Find / Replace · workspace text" if self.replace_enabled else "Find · workspace text", markup=False)
            yield FileInput(self.pattern, placeholder="Find content (regex)", id="workspace-find")
            yield FileInput(self.replacement, placeholder=r"Replace with (backreferences: \1)", id="workspace-replacement")
            yield Static("Preview only · files open in the editor; save each file explicitly.", id="replace-status", markup=False)
            yield OptionList(id="replace-results")
            yield Button("Preview replacements in editor" if self.replace_enabled else "Open matching file", id="replace-preview", compact=True, variant="primary")
            yield Button("Cancel", id="replace-cancel", compact=True)
    def on_mount(self):
        self.query_one("#workspace-replacement", Input).display = self.replace_enabled
        self.query_one("#workspace-find", Input).focus()
    def action_close_search(self):
        self.dismiss(None)
    def on_input_changed(self, event: Input.Changed):
        self.results = []
        options = self.query_one("#replace-results", OptionList)
        options.clear_options()
        pattern = self.query_one("#workspace-find", Input).value
        replacement = self.query_one("#workspace-replacement", Input).value
        self.post_message(WorkspaceFiles.FilterChanged(self.session_id, content_filter=pattern, replacement=replacement))
        status = self.query_one("#replace-status", Static)
        if not pattern:
            status.update("Enter a content regex · preview only; disk unchanged")
            return
        try:
            # Validate even when no readable files exist.
            replacement_preview("", pattern, replacement)
            entries, truncated = self.files.entries()
            total_bytes, scanned, skipped = 0, 0, 0
            began = time.monotonic()
            for relative, directory in entries:
                if directory:
                    continue
                if scanned >= 256 or total_bytes >= 4 * 1024 * 1024 or time.monotonic() - began > .15:
                    truncated = True
                    break
                scanned += 1
                try:
                    snapshot = self.files.read(relative)
                except (FileSafetyError, OSError):
                    skipped += 1
                    continue
                total_bytes += len(snapshot.text.encode())
                text, count = replacement_preview(snapshot.text, pattern, replacement if self.replace_enabled else r"\g<0>")
                if count:
                    self.results.append((snapshot, text, count))
                    options.add_option(Text(f"{relative} · {count} matches"))
            if self.results:
                options.highlighted = 0
            status.update(f"{len(self.results)} matching files · {scanned} scanned · {skipped} skipped" +
                (" · partial results (scan limit reached)" if truncated else "") + " · disk unchanged")
        except (regex.error, TimeoutError, FileSafetyError, IndexError) as error:
            self.results = []
            options.clear_options()
            status.update(f"Find/replace: {error}")
    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self._preview(event.option_index)
    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == "replace-cancel":
            self.dismiss(None)
        elif event.button.id == "replace-preview":
            self._preview(self.query_one("#replace-results", OptionList).highlighted)
    def _preview(self, index: int | None):
        if index is None or index >= len(self.results):
            return
        snapshot, text, count = self.results[index]
        editor = FileEditorScreen(self.files, snapshot)
        self.app.push_screen(editor)
        def stage():
            widget = editor.query_one("#file-editor", TextArea)
            lines = snapshot.text.split("\n")
            if self.replace_enabled:
                widget.replace(text, (0, 0), (len(lines) - 1, len(lines[-1])), maintain_selection_offset=False)
            editor.query_one("#file-editor-status", Static).update(f"{count} matches · disk unchanged" + (" · replacement preview · Ctrl+Z undo · Ctrl+Shift+S save" if self.replace_enabled else ""))
        editor.call_after_refresh(stage)


class WorkspaceFiles(Vertical):
    """Mount inside the chat layout only; arrow keys navigate the standard Tree."""
    DEFAULT_CSS = """
    WorkspaceFiles { width: 30; min-width: 20; max-width: 40; height: 1fr; border-right: solid $panel; padding: 0 1; }
    #files-view-tabs { height: 1; margin-bottom: 1; }
    #files-view-tabs Button { width: 1fr; min-width: 0; height: 1; min-height: 1; border: none; padding: 0; margin: 0; content-align: center middle; background: #242424; color: #d8d8d8; }
    #files-view-tabs Button.active { background: #30496a; color: white; }
    #files-explorer-panel, #files-git-panel { height: 1fr; }
    #files-git-panel { display: none; }
    #git-change-counts { height: 2; color: #a9bbd2; }
    #git-changes-heading, #git-staged-heading { height: 1; margin-top: 1; color: #d8d8d8; }
    #git-changes-list, #git-staged-list { height: 1fr; background: #141c28; }
    #git-panel-status { height: auto; color: $text-muted; margin-top: 1; }
    #file-active-directory { height: 2; color: #a9bbd2; }
    #file-search { height: 1; min-height: 1; border: none; padding: 0 1; background: #273346; color: #f1f5ff; margin-top: 1; }
    #file-search:focus { background: #304f75; }
    #files-folder-actions { height: 1; margin-top: 1; }
    #files-folder-actions Button { width: 1fr; min-width: 0; margin: 0; padding: 0; }
    #files-paste { display: none; }
    #files-actions { height: 1; margin-top: 1; }
    #files-actions Button { width: 1fr; min-width: 0; margin: 0; }
    #files-find { margin-right: 1; }
    #workspace-file-tree { height: 1fr; }
    #file-search-status { height: auto; color: $text-muted; margin-top: 1; }
    #file-inline-actions { display: none; height: auto; margin: 1 0; padding: 0 1; border: solid #668ac1; background: #192434; }
    #file-inline-title { height: auto; max-height: 3; color: #bdc9df; }
    #file-inline-actions Button { width: 1fr; min-width: 0; }
    WorkspaceFiles Button { height: 1; min-height: 1; border: none; padding: 0 1; margin-top: 1; background: #375679; color: #f1f5ff; }
    """
    class FilterChanged(Message):
        def __init__(self, session_id, *, path_filter=None, content_filter=None, replacement=None):
            super().__init__()
            self.session_id, self.path_filter = session_id, path_filter
            self.content_filter, self.replacement = content_filter, replacement

    class OpenFolder(Message):
        def __init__(self, session_id):
            super().__init__()
            self.session_id = session_id

    class PreviewRequested(Message):
        def __init__(self, session_id, snapshot, line, *, files, edit=False):
            super().__init__()
            self.session_id, self.snapshot, self.line = session_id, snapshot, line
            self.files, self.edit = files, edit

    class GitDiffRequested(Message):
        def __init__(self, session_id, entry: GitChange, *, files, staged: bool):
            super().__init__()
            self.session_id, self.entry, self.files, self.staged = session_id, entry, files, staged

    BINDINGS = [
        Binding("escape", "close_file_options", "Close file options", show=False),
    ]

    def __init__(self, root: Path | None = None, **kwargs):
        super().__init__(**kwargs)
        self.files = ProjectFiles(root or Path.cwd())
        self.session_id = None
        self.content_filter, self.replacement = "", ""
        self.selected_file, self.selected_line = None, None
        self._cut_file: str | None = None
        self._cut_session_id: str | None = None
        self._paste_destination: str | None = None
        self._drag_source = None
        self._mouse_down_node = None
        self.git_changes: list[GitChange] = []
        self.git_staged: list[GitChange] = []
        self._git_generation = 0
        self._last_git_selection: tuple[str, int] | None = None
        self._closed_git_selection: tuple[str, int] | None = None
    def compose(self) -> ComposeResult:
        yield Static("Workspace files")
        yield Static(str(self.files.root), id="file-active-directory", markup=False)
        yield Static("", id="file-git-branch", markup=False)
        with Horizontal(id="files-view-tabs"):
            yield Button("Explorer", id="files-tab-explorer", classes="active", compact=True)
            yield Button("Git Changes", id="files-tab-git", compact=True)
        with Vertical(id="files-explorer-panel"):
            with Horizontal(id="files-folder-actions"):
                yield Button("Open folder", id="files-open-folder", compact=True)
                yield Button("New file", id="files-create-new", compact=True)
                yield Button("Paste", id="files-paste", compact=True)
            yield FileInput(placeholder="Search files + contents (regex)", id="file-search")
            with Horizontal(id="files-actions"):
                yield Button("Find", id="files-find", compact=True)
                yield Button("Replace", id="files-replace", compact=True)
            yield Button("Refresh", id="files-refresh", compact=True)
            yield Static("Drag a file onto a folder to move it", id="file-tree-hint", markup=False)
            yield Static("", id="file-search-status", markup=False)
            with Vertical(id="file-inline-actions"):
                yield Static("", id="file-inline-title", markup=False)
                yield Button("Rename", id="file-inline-rename", compact=True)
                yield Button("Copy path", id="file-inline-copy-path", compact=True)
                yield Button("Copy relative path", id="file-inline-copy-relative", compact=True)
                yield Button("Duplicate", id="file-inline-duplicate", compact=True)
                yield Button("Delete", id="file-inline-delete", compact=True, variant="error")
                yield Button("Cut", id="file-inline-cut", compact=True)
                yield Button("Paste", id="file-inline-paste", compact=True)
            yield ExplorerTree(Text(self.files.root.name), id="workspace-file-tree")
        with Vertical(id="files-git-panel"):
            yield Static("Changes · 0    Staged · 0", id="git-change-counts", markup=False)
            yield Static("Changes", id="git-changes-heading")
            yield OptionList(id="git-changes-list")
            yield Static("Staged Changes", id="git-staged-heading")
            yield OptionList(id="git-staged-list")
            yield Static("Select a file to compare it side-by-side.", id="git-panel-status", markup=False)
            yield Button("Refresh Git", id="git-refresh", compact=True)
    def on_mount(self):
        self.refresh_files()
        self.set_interval(2.0, self.refresh_git_status)
        self.refresh_git_status()

    def _select_view(self, view: str):
        explorer = view == "explorer"
        self.query_one("#files-explorer-panel").display = explorer
        self.query_one("#files-git-panel").display = not explorer
        self.query_one("#files-tab-explorer", Button).set_class(explorer, "active")
        self.query_one("#files-tab-git", Button).set_class(not explorer, "active")
        if not explorer:
            self.refresh_git_status()
            listing = self.query_one("#git-changes-list", OptionList)
            if self.git_changes:
                listing.focus()
                self._request_git_diff(listing.id, listing.highlighted)
            elif self.git_staged:
                staged = self.query_one("#git-staged-list", OptionList)
                staged.focus()
                self._request_git_diff(staged.id, staged.highlighted)

    @work(group="workspace-git-status", exclusive=True)
    async def refresh_git_status(self):
        """Poll Git status without blocking the chat; only reads the session repository."""
        root = self.files.root
        self._git_generation += 1
        generation = self._git_generation
        process = None
        try:
            environment = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT", "WINDIR") if key in os.environ}
            environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
            process = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                cwd=root, env=environment, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output = await asyncio.wait_for(process.stdout.read(1_000_001), 2)
            if len(output) > 1_000_000:
                raise FileSafetyError("Git status output is too large to display.")
            code = await asyncio.wait_for(process.wait(), 2)
            if code != 0:
                raise FileSafetyError("This session directory is not a Git repository.")
            changes, staged = self._parse_git_status(output)
            if generation != self._git_generation or root != self.files.root:
                return
            self.git_changes, self.git_staged = changes, staged
            self._render_git_status()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if generation == self._git_generation:
                self.git_changes, self.git_staged = [], []
                self._render_git_status(str(error))
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    def _parse_git_status(self, output: bytes) -> tuple[list[GitChange], list[GitChange]]:
        records = output.split(b"\0")
        changes: list[GitChange] = []
        staged: list[GitChange] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 4:
                continue
            status = record[:2].decode("ascii", errors="replace")
            path = os.fsdecode(record[3:])
            if "R" in status or "C" in status:
                index += 1  # porcelain -z follows a rename/copy with the original path
            try:
                self.files.parts(path)
            except FileSafetyError:
                continue
            entry = GitChange(path, status[0], status[1])
            if entry.changed:
                changes.append(entry)
            if entry.staged:
                staged.append(entry)
        return changes, staged

    def _render_git_status(self, error: str | None = None):
        if not self.is_mounted:
            return
        changes_list = self.query_one("#git-changes-list", OptionList)
        staged_list = self.query_one("#git-staged-list", OptionList)
        old_change_index, old_staged_index = changes_list.highlighted, staged_list.highlighted
        changes_list.clear_options()
        staged_list.clear_options()
        for entry in self.git_changes:
            changes_list.add_option(Text(entry.label))
        for entry in self.git_staged:
            staged_list.add_option(Text(entry.label))
        if self.git_changes:
            changes_list.highlighted = min(old_change_index or 0, len(self.git_changes) - 1)
        if self.git_staged:
            staged_list.highlighted = min(old_staged_index or 0, len(self.git_staged) - 1)
        self.query_one("#git-change-counts", Static).update(
            f"Changes · {len(self.git_changes)}    Staged · {len(self.git_staged)}")
        self.query_one("#git-panel-status", Static).update(
            error or ("Select a file to compare it side-by-side." if self.git_changes or self.git_staged else "Working tree clean."))
        if self.query_one("#files-git-panel").display and self.app.focused is not None:
            focused_id = self.app.focused.id
            target = None
            if focused_id in {"files-tab-git", "git-refresh"}:
                target = changes_list if self.git_changes else staged_list
                if target.option_count:
                    target.focus()
            # Refreshing Git status must never reopen a comparison that the
            # user deliberately closed. Diffs are opened by selection events.

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self._request_git_diff(event.option_list.id, event.option_index, explicit=True)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted):
        # OptionSelected is mouse/Enter activation; highlighted fires as the user
        # moves with arrows, so keep the side-by-side diff in sync with focus.
        self._request_git_diff(event.option_list.id, event.option_index)

    def _request_git_diff(self, list_id: str | None, index: int | None, *, explicit=False):
        if index is None:
            return
        selection = (list_id or "", index)
        # Closing a diff should leave the Git panel visible without a queued
        # highlight event immediately opening that same item again. Moving to
        # another item or explicitly activating it re-enables opening.
        if selection == self._closed_git_selection and not explicit:
            return
        if explicit:
            self._closed_git_selection = None
        if list_id == "git-changes-list" and index < len(self.git_changes):
            entry, staged = self.git_changes[index], False
        elif list_id == "git-staged-list" and index < len(self.git_staged):
            entry, staged = self.git_staged[index], True
        else:
            return
        self._last_git_selection = selection
        self.post_message(self.GitDiffRequested(self.session_id, entry, files=self.files, staged=staged))

    def dismiss_git_diff(self):
        """Remember the current selection until the user selects again."""
        self._closed_git_selection = self._last_git_selection
    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "file-search" and event.value == event.input.value:
            self.refresh_files(event.value)
            self.post_message(self.FilterChanged(self.session_id, path_filter=event.value))
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id in {"files-tab-explorer", "files-tab-git"}:
            self._select_view("git" if event.button.id == "files-tab-git" else "explorer")
            event.stop()
        elif event.button.id == "git-refresh":
            self.refresh_git_status()
            event.stop()
        elif event.button.id == "files-open-folder":
            event.stop()
            self.post_message(self.OpenFolder(self.session_id))
        elif event.button.id == "files-create-new":
            event.stop()
            destination = self._selected_destination()
            self.app.push_screen(NewFileScreen(destination), lambda name: self._create_new_file(destination, name))
        elif event.button.id == "files-paste":
            event.stop()
            self._paste_cut_file(self._selected_destination())
        elif event.button.id == "file-inline-rename":
            event.stop()
            if self.selected_file:
                relative = self.selected_file
                initial = Path(relative).name
                parent = str(Path(relative).parent)
                self.app.push_screen(NewFileScreen(parent if parent != "." else "", heading="Rename file in:", initial=initial, action="Rename"),
                    lambda new_name: self._rename_file(relative, new_name))
        elif event.button.id == "file-inline-copy-path":
            event.stop()
            if self.selected_file:
                self.app.copy_to_clipboard(str(self.files.root / self.selected_file))
                self.notify("Absolute path copied")
        elif event.button.id == "file-inline-copy-relative":
            event.stop()
            if self.selected_file:
                self.app.copy_to_clipboard(self.selected_file)
                self.notify("Relative path copied")
        elif event.button.id == "file-inline-duplicate":
            event.stop()
            if self.selected_file:
                relative = self.selected_file
                path = Path(relative)
                initial = f"{path.stem} copy{path.suffix}"
                parent = str(path.parent)
                self.app.push_screen(NewFileScreen(parent if parent != "." else "", heading="Duplicate into:", initial=initial, action="Duplicate"),
                    lambda new_name: self._duplicate_file(relative, new_name))
        elif event.button.id == "file-inline-cut":
            event.stop()
            if self.selected_file:
                self._cut_file = self.selected_file
                self._cut_session_id = self.session_id
                self.query_one("#files-paste").display = True
                self.query_one("#file-search-status", Static).update(
                    f"Cut {self._cut_file} · select a destination folder, then choose Paste")
                self.action_close_file_options(focus=False)
        elif event.button.id == "file-inline-paste":
            event.stop()
            self._paste_cut_file()
        elif event.button.id == "file-inline-delete":
            event.stop()
            if self.selected_file:
                relative = self.selected_file
                self.app.push_screen(
                    FileOperationConfirmScreen("Delete file?", relative, destructive=True),
                    lambda confirmed: self._delete_file(relative, confirmed),
                )
        elif event.button.id == "files-refresh":
            self.refresh_files(self.query_one("#file-search", Input).value)
        elif event.button.id in {"files-find", "files-replace"}:
            event.stop()
            self.app.push_screen(WorkspaceReplaceScreen(self.files, session_id=self.session_id,
                pattern=self.content_filter or self.query_one("#file-search", Input).value,
                replacement=self.replacement, replace_enabled=event.button.id == "files-replace"))
    def set_workspace(self, root: Path, session_id: str, path_filter="", content_filter="", replacement=""):
        self.action_close_file_options(focus=False)
        self._cut_file = None
        self._cut_session_id = None
        self._paste_destination = None
        self.query_one("#files-paste").display = False
        self.session_id = session_id
        self.content_filter, self.replacement = content_filter, replacement
        # If a saved directory becomes unavailable, do not silently browse the
        # previous session's files or substitute the launch directory.
        self.query_one("#file-active-directory", Static).update(str(root))
        try:
            self.files = ProjectFiles(root)
        except (FileSafetyError, OSError) as error:
            self.query_one("#workspace-file-tree", Tree).clear()
            self.query_one("#file-search-status", Static).update(f"Directory unavailable: {error}")
            for selector in ("#workspace-file-tree", "#file-search", "#files-find", "#files-replace", "#files-refresh", "#files-create-new"):
                self.query_one(selector).disabled = True
            return
        self.disabled = False
        for selector in ("#workspace-file-tree", "#file-search", "#files-find", "#files-replace", "#files-refresh", "#files-create-new"):
            self.query_one(selector).disabled = False
        self.query_one("#workspace-file-tree", Tree).root.set_label(Text(self.files.root.name))
        self.query_one("#workspace-file-tree", Tree).clear()
        self.query_one("#file-search", Input).value = path_filter
        self.refresh_files(path_filter)
        self.refresh_git_status()
    def refresh_files(self, pattern: str = ""):
        tree = self.query_one("#workspace-file-tree", Tree)
        status = self.query_one("#file-search-status", Static)
        try:
            if len(pattern) > 256:
                raise FileSafetyError("Regex is limited to 256 characters.")
            compiled = regex.compile(pattern, regex.IGNORECASE) if pattern else None
            entries, truncated = self.files.entries()
            started = time.monotonic()
            matches = []
            previews = {}
            scanned, skipped, read_bytes = 0, 0, 0
            for relative, is_dir in entries:
                if time.monotonic() - started > .1:
                    truncated = True
                    break
                path_match = compiled is None or compiled.search(relative, timeout=.003)
                if path_match:
                    matches.append((relative, is_dir))
                if compiled is not None and not is_dir:
                    if scanned >= 256 or read_bytes >= 4 * 1024 * 1024:
                        truncated = True
                        continue
                    scanned += 1
                    try:
                        snapshot = self.files.read(relative)
                    except (FileSafetyError, OSError):
                        skipped += 1
                        continue
                    read_bytes += len(snapshot.text.encode())
                    snippets, seen_lines = [], set()
                    for match in compiled.finditer(snapshot.text, timeout=.003):
                        line = snapshot.text.count("\n", 0, match.start()) + 1
                        if line in seen_lines:
                            continue
                        seen_lines.add(line)
                        start = snapshot.text.rfind("\n", 0, match.start()) + 1
                        end = snapshot.text.find("\n", match.start())
                        end = len(snapshot.text) if end < 0 else end
                        start = max(start, match.start() - 30)
                        end = min(end, start + 90)
                        excerpt = snapshot.text[start:end].replace("\t", " ")
                        label = Text(f"{line}: ", style="#a9bbd2")
                        offset = len(label)
                        label.append(excerpt, style="#b9c8dc")
                        label.stylize("bold #ffd166", offset + match.start() - start,
                                      offset + min(match.end(), end) - start)
                        snippets.append((line, label))
                        if len(snippets) >= 3:
                            break
                    if snippets:
                        previews[relative] = snippets
                        if not path_match:
                            matches.append((relative, False))
        except (regex.error, TimeoutError, FileSafetyError) as error:
            tree.clear()
            status.update(f"Search: {error}")
            return
        tree.clear()
        nodes = {"": tree.root}
        for relative, is_dir in matches:
            parts = Path(relative).parts
            for index in range(1, len(parts) + 1):
                path = str(Path(*parts[:index]))
                if path in nodes:
                    continue
                parent = "" if index == 1 else str(Path(*parts[:index - 1]))
                directory = index < len(parts) or is_dir
                nodes[path] = nodes[parent].add(Text(parts[index - 1]), data=FolderTarget(path) if directory else path,
                    allow_expand=directory or path in previews, expand=bool(pattern))
            for line, label in previews.get(relative, []):
                nodes[relative].add_leaf(label, data=FileMatch(relative, line))
        tree.root.expand()
        status.update(f"{len(matches)} matching entries" + (f" · {skipped} skipped" if skipped else "") +
            (" · partial results (scan limit reached)" if truncated else ""))
    def on_click(self, event):
        tree = self.query_one("#workspace-file-tree", Tree)
        if event.widget is not tree or event.button != 1:
            return
        # Prefer mouse-down's hit-test; some terminal mouse protocols report
        # the generated Click style one row above the actual pointer position.
        node = self._mouse_down_node or self._mouse_node(tree, event)
        self._mouse_down_node = None
        data = node.data if node else None
        if not isinstance(data, (str, FileMatch)):
            self.selected_file, self.selected_line = None, None
            self.query_one("#file-inline-actions").display = False
            return
        match = data
        self.selected_file = match.relative if isinstance(match, FileMatch) else match
        self.selected_line = match.line if isinstance(match, FileMatch) else None
        if event.chain >= 2:
            self.query_one("#file-inline-actions").display = False
            self._open_file(self.selected_file, "edit", self.selected_line)
        else:
            self.query_one("#file-inline-actions").display = False

    def on_mouse_down(self, event):
        tree = self.query_one("#workspace-file-tree", Tree)
        if event.widget is not tree or event.button not in {1, 3}:
            return
        node = self._mouse_node(tree, event)
        self._mouse_down_node = node
        if node is not None and event.button == 1:
            self._drag_source = node
            tree.capture_mouse()

    def on_mouse_move(self, event):
        if self._drag_source is None:
            return
        tree = self.query_one("#workspace-file-tree", Tree)
        target = self._mouse_node(tree, event)
        source = self._file_path_for_node(self._drag_source)
        destination = self._folder_for_node(target)
        if source and destination is not None:
            self.query_one("#file-search-status", Static).update(f"Move {source} → {destination or '.'}; release to confirm")

    def on_mouse_up(self, event):
        tree = self.query_one("#workspace-file-tree", Tree)
        if event.widget is tree and event.button == 3:
            node = self._mouse_down_node or self._mouse_node(tree, event)
            self._mouse_down_node = None
            self._show_file_options(node)
            event.stop()
            return
        if self._drag_source is None:
            return
        source_node, self._drag_source = self._drag_source, None
        tree.release_mouse()
        target = self._mouse_node(tree, event)
        source = self._file_path_for_node(source_node)
        destination = self._folder_for_node(target)
        if event.button == 1 and source and destination is not None:
            self._mouse_down_node = None
            event.stop()
            if str(Path(source).parent) == (destination or "."):
                self.query_one("#file-search-status", Static).update("That file is already in this folder")
                return
            self.app.push_screen(
                FileOperationConfirmScreen("Move file?", f"{source} → {destination or '.'}"),
                lambda confirmed: self._move_file(source, destination, confirmed),
            )
        elif event.button == 1:
            self.query_one("#file-search-status", Static).update("Drag a file onto a folder to move it")

    def _show_file_options(self, node):
        data = node.data if node else None
        tree = self.query_one("#workspace-file-tree", Tree)
        if isinstance(data, FolderTarget):
            self.selected_file, self.selected_line = None, None
            destination = data.relative
            title = f"{destination}/"
            is_file = False
        elif node is tree.root:
            self.selected_file, self.selected_line = None, None
            destination = ""
            title = self.files.root.name + "/"
            is_file = False
        elif isinstance(data, (str, FileMatch)):
            match = data
            self.selected_file = match.relative if isinstance(match, FileMatch) else match
            self.selected_line = match.line if isinstance(match, FileMatch) else None
            parent = Path(self.selected_file).parent
            destination = "" if str(parent) == "." else str(parent)
            title = self.selected_file
            is_file = True
        else:
            self.query_one("#file-inline-actions").display = False
            return
        self._paste_destination = destination
        self.query_one("#file-inline-title", Static).update(title)
        file_actions = ("rename", "copy-path", "copy-relative", "duplicate", "delete", "cut")
        for action in file_actions:
            self.query_one(f"#file-inline-{action}").display = is_file
        can_paste = bool(self._cut_file and self._cut_session_id == self.session_id
                         and destination != (str(Path(self._cut_file).parent) if str(Path(self._cut_file).parent) != "." else ""))
        self.query_one("#file-inline-paste").display = can_paste
        self.query_one("#file-inline-actions").display = is_file or can_paste
        if is_file:
            self.query_one("#file-inline-rename", Button).focus()
        elif can_paste:
            self.query_one("#file-inline-paste", Button).focus()

    def _paste_cut_file(self, destination=None):
        source = self._cut_file
        if destination is None:
            destination = self._paste_destination
        session_id = self._cut_session_id
        if not source or destination is None or session_id != self.session_id:
            self.notify("Cut a file, then choose a destination folder.", severity="warning")
            return
        if self.app._ensure_active_session().id != session_id:
            self.notify("The active session changed; no file was moved.", severity="warning")
            return
        if destination == (str(Path(source).parent) if str(Path(source).parent) != "." else ""):
            self.notify("That file is already in this folder.", severity="warning")
            return
        self.app.push_screen(
            FileOperationConfirmScreen("Move file?", f"{source} → {destination or '.'}"),
            lambda confirmed: self._paste_cut_file_confirmed(source, destination, session_id, confirmed),
        )

    def _paste_cut_file_confirmed(self, source, destination, session_id, confirmed):
        if not confirmed:
            return
        if self.session_id != session_id or self._cut_file != source:
            self.notify("The session or cut selection changed; no file was moved.", severity="warning")
            return
        self._move_file(source, destination, True)

    @staticmethod
    def _mouse_node(tree, event):
        meta = event.style.meta if event.style else {}
        node_id = meta.get("node") if meta else None
        if node_id is not None:
            try:
                return tree.get_node_by_id(node_id)
            except Exception:
                pass
        line = meta.get("line") if meta else None
        if line is None and tree.hover_line >= 0:
            line = tree.hover_line
        return tree.get_node_at_line(line) if line is not None else None

    @staticmethod
    def _file_path_for_node(node):
        if node is None:
            return None
        data = node.data
        if isinstance(data, FileMatch):
            return data.relative
        return data if isinstance(data, str) else None

    @staticmethod
    def _folder_for_node(node):
        if node is None:
            return None
        if isinstance(node.data, FolderTarget):
            return node.data.relative
        return "" if node.parent is None else None

    def _close_matching_preview(self, relative):
        pane = self.app.query_one("#session-file-preview", FilePreviewPane)
        if pane.snapshot and pane.snapshot.relative == relative:
            pane.action_close()

    def _move_file(self, source, destination, confirmed, new_name=None):
        if not confirmed:
            self.refresh_files(self.query_one("#file-search", Input).value)
            return
        if self.app._file_editor_dirty():
            self.notify("Save or discard the open file edits before moving it.", severity="warning")
            return
        try:
            target = self.files.move_file(source, destination, new_name)
        except (FileSafetyError, OSError) as error:
            self.notify(str(error), severity="error")
            self.refresh_files(self.query_one("#file-search", Input).value)
            return
        self._close_matching_preview(source)
        if self._cut_file == source:
            self._cut_file = None
            self._cut_session_id = None
            self.query_one("#files-paste").display = False
        self.action_close_file_options(focus=False)
        self.refresh_files(self.query_one("#file-search", Input).value)
        self.notify(f"Moved {source} to {target}")

    def _rename_file(self, source, new_name):
        if new_name is None or new_name == Path(source).name:
            return
        parent = str(Path(source).parent)
        parent = "" if parent == "." else parent
        target = str(Path(parent, new_name)) if parent else new_name
        self.app.push_screen(FileOperationConfirmScreen("Rename file?", f"{source} → {target}", affirmative="Rename"),
            lambda confirmed: self._move_file(source, parent, confirmed, new_name))

    def _duplicate_file(self, source, new_name):
        if new_name is None:
            return
        if self.app._file_editor_dirty():
            self.notify("Save or discard the open file edits before duplicating it.", severity="warning")
            return
        try:
            duplicate = self.files.duplicate_file(source, new_name)
        except (FileSafetyError, OSError) as error:
            self.notify(str(error), severity="error")
            return
        self.refresh_files(self.query_one("#file-search", Input).value)
        self.notify(f"Duplicated {source} as {duplicate.relative}")

    def _delete_file(self, relative, confirmed):
        if not confirmed:
            return
        if self.app._file_editor_dirty():
            self.notify("Save or discard the open file edits before deleting it.", severity="warning")
            return
        try:
            self.files.delete_file(relative)
        except (FileSafetyError, OSError) as error:
            self.notify(str(error), severity="error")
            return
        self._close_matching_preview(relative)
        self.action_close_file_options(focus=False)
        self.refresh_files(self.query_one("#file-search", Input).value)
        self.notify(f"Deleted {relative}")

    def _selected_destination(self):
        tree = self.query_one("#workspace-file-tree", Tree)
        node = tree.cursor_node
        if node is None or node is tree.root:
            return ""
        data = node.data
        if isinstance(data, FolderTarget):
            return data.relative
        if isinstance(data, FileMatch):
            destination = str(Path(data.relative).parent)
        elif isinstance(data, str):
            destination = str(Path(data).parent)
        else:
            return ""
        return "" if destination == "." else destination

    def _create_new_file(self, destination, name):
        if name is None:
            return
        if self.app._ensure_active_session().id != self.session_id:
            self.notify("The active session changed; no file was created.", severity="warning")
            return
        if self.app._file_editor_dirty():
            self.notify("Save or discard the open file edits before creating another file.", severity="warning")
            return
        relative = str(Path(destination) / name) if destination else name
        try:
            snapshot = self.files.create(relative)
        except (FileSafetyError, OSError) as error:
            self.notify(str(error), severity="error")
            return
        self.refresh_files(self.query_one("#file-search", Input).value)
        self.notify(f"Created {relative}")
        self.post_message(self.PreviewRequested(self.session_id, snapshot, None, files=self.files, edit=True))

    def action_close_file_options(self, focus=True):
        self.selected_file, self.selected_line = None, None
        panel = self.query_one("#file-inline-actions")
        was_open = panel.display
        panel.display = False
        if focus and was_open:
            self.query_one("#workspace-file-tree", Tree).focus()
    def _open_file(self, relative: str, action: str | None, line: int | None = None):
        if action is None:
            return
        try:
            snapshot = self.files.read(relative)
        except (FileSafetyError, OSError) as error:
            self.notify(str(error), severity="error")
            return
        self.post_message(self.PreviewRequested(self.session_id, snapshot, line, files=self.files, edit=action == "edit"))
