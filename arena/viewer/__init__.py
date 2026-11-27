"""Standalone HTML replay viewer: the template with a replay embedded in it."""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("index.html")
_PLACEHOLDER = '<script id="replay-data" type="application/json">null</script>'

_SKELETON_HEAD = (
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
)


def render_html(replay: dict | None, standalone: bool = True) -> str:
    """HTML for ``replay``. ``standalone`` wraps it in a full document for opening from disk."""
    page = TEMPLATE.read_text(encoding="utf-8")
    if replay is not None:
        data = json.dumps(replay, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
        page = page.replace(_PLACEHOLDER, f'<script id="replay-data" type="application/json">{data}</script>')
    if not standalone:
        return page
    head, sep, body = page.partition("</style>")
    return f"{_SKELETON_HEAD}{head}{sep}\n</head>\n<body>{body}\n</body>\n</html>\n"


def write_html(replay: dict | None, path: str | Path, standalone: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(replay, standalone), encoding="utf-8")
    return path
