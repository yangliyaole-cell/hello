"""Loading bot programs from disk."""

from __future__ import annotations

from pathlib import Path

from .meter import METER
from .sandbox import validate_source


class BotProgram:
    """One team's compiled bot. Each robot gets its own module instance of it."""

    def __init__(self, name: str, source: str, filename: str):
        self.name = name
        self.source = source
        self.filename = filename
        self.code = None
        tree, self.errors = validate_source(source, filename)
        if not self.errors:
            try:
                self.code = compile(tree, filename, "exec")
            except (SyntaxError, ValueError) as e:  # pragma: no cover - ast.parse already succeeded
                self.errors.append(f"compile error: {e}")
            else:
                METER.instrument_code(self.code)

    @property
    def ok(self) -> bool:
        return self.code is not None

    @classmethod
    def from_path(cls, path: str | Path) -> "BotProgram":
        """``path`` is a bot directory containing bot.py, or a .py file."""
        p = Path(path)
        file = p / "bot.py" if p.is_dir() else p
        name = p.name if p.is_dir() else p.stem
        if not file.is_file():
            prog = cls.__new__(cls)
            prog.name, prog.source, prog.filename, prog.code = name, "", str(file), None
            prog.errors = [f"no bot file found at {file}"]
            return prog
        return cls(name, file.read_text(encoding="utf-8"), str(file))

    @classmethod
    def from_source(cls, name: str, source: str) -> "BotProgram":
        return cls(name, source, f"<bot {name}>")
