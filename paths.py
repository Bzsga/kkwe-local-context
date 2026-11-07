"""Editor location selection shared by the command line and desktop manager."""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent
EDITOR_ENV = "KKLC_EDITOR_ROOT"


def launcher_settings(tool_root: Path = TOOL_ROOT) -> dict:
    """Read optional local settings without creating or changing any files."""
    try:
        value = json.loads((tool_root / "state/launcher.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve_editor_root(explicit: str | Path | None = None, *,
                        tool_root: Path = TOOL_ROOT,
                        environ: Mapping[str, str] | None = None) -> Path | None:
    """Prefer explicit, environment, saved, then an existing legacy tools layout.

    Explicit and configured locations are returned even if unavailable so the
    editor's normal compatibility checks can explain the specific problem.
    An unrelated standalone checkout never supplies a guessed editor location.
    """
    environment = os.environ if environ is None else environ
    saved = launcher_settings(tool_root).get("editor_root")
    for value in (explicit, environment.get(EDITOR_ENV), saved):
        if isinstance(value, (str, Path)) and str(value).strip():
            return Path(str(value).strip()).expanduser().resolve()
    if tool_root.parent.name.casefold() == "tools":
        candidate = tool_root.parent.parent / "KKWE插件"
        if (candidate / "share/script/compile/wave.lua").is_file():
            return candidate.resolve()
    return None
