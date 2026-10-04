"""Local image attachments; never capture the clipboard except on user paste."""

import base64
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageGrab

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def validate_image(path: str | Path) -> Path:
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image must be an existing file smaller than 10 MB.")
    with Image.open(path) as image:
        if image.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
            raise ValueError("Use a PNG, JPEG, GIF, or WebP image.")
        image.verify()
    return path


def paste_image(directory: Path) -> Path | None:
    clipboard = ImageGrab.grabclipboard()
    if isinstance(clipboard, list):
        for name in clipboard:
            try:
                return validate_image(name)
            except (OSError, ValueError):
                continue
        return None
    if not isinstance(clipboard, Image.Image):
        return None
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"paste-{uuid4().hex}.png"
    clipboard.save(path, format="PNG")
    path.chmod(0o600)
    return validate_image(path)


def image_data(path: str):
    validated = validate_image(path)
    with Image.open(validated) as image:
        media = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}[image.format]
    return media, base64.b64encode(validated.read_bytes()).decode()


def task_images(task) -> list[str]:
    return [str(validate_image(path)) for path in task.context.get("images", [])][:8]
