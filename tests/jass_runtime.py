"""Execute the actual KKLC plain-JASS source with explicit native mocks.

This deliberately small, strict translator accepts only constructs in the runtime;
it is not a second implementation of the allocator or a Warcraft VM emulator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(eq=False)
class Handle:
    id: int
    action: Callable[[], None] | None = None
    condition: Callable[[], bool] | None = None


def _expression(text: str) -> str:
    parts = re.split(r'("(?:[^"\\]|\\.)*")', text)
    for index in range(0, len(parts), 2):
        for old, new in [("null", "None"), ("true", "True"), ("false", "False")]:
            parts[index] = re.sub(rf"\b{old}\b", new, parts[index])
    return "".join(parts)


def translate_jass(source: str) -> str:
    """Strict source-to-source translation; unsupported syntax fails closed."""
    output: list[str] = []
    globals_: list[str] = []
    indent = 0
    in_globals = False
    in_function = False
    for line_number, raw in enumerate(source.splitlines(), 1):
        # Runtime comments never contain significant // inside strings.
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if line == "globals":
            in_globals = True
            continue
        if line == "endglobals":
            in_globals = False
            continue
        if in_globals:
            match = re.fullmatch(r"\w+ (\w+) = (.+)", line)
            if not match:
                raise ValueError(f"Unsupported global at JASS line {line_number}: {line}")
            globals_.append(match[1])
            output.append(f"{match[1]} = {_expression(match[2])}")
            continue
        match = re.fullmatch(r"function (\w+) takes (.+) returns (\w+)", line)
        if match:
            if in_function or indent:
                raise ValueError(f"Nested function at JASS line {line_number}")
            arguments = [] if match[2] == "nothing" else [p.strip().split()[1] for p in match[2].split(",")]
            output.append(f"def {match[1]}({', '.join(arguments)}):")
            output.append("    global " + ", ".join(globals_))
            in_function = True
            indent = 1
            continue
        if line == "endfunction":
            if indent != 1:
                raise ValueError(f"Unbalanced function at JASS line {line_number}")
            in_function = False
            indent = 0
            continue
        if not in_function:
            raise ValueError(f"Unexpected top-level JASS line {line_number}: {line}")
        if line in ("endif", "endloop"):
            indent -= 1
            continue
        if line == "else":
            output.append("    " * (indent - 1) + "else:")
            continue
        if line.startswith("elseif ") and line.endswith(" then"):
            output.append("    " * (indent - 1) + "elif " + _expression(line[7:-5]) + ":")
            continue
        if line.startswith("if ") and line.endswith(" then"):
            output.append("    " * indent + "if " + _expression(line[3:-5]) + ":")
            indent += 1
            continue
        if line == "loop":
            output.append("    " * indent + "while True:")
            indent += 1
            continue
        if line.startswith("exitwhen "):
            output.append("    " * indent + "if " + _expression(line[9:]) + ": break")
            continue
        match = re.fullmatch(r"local (\w+) (\w+)(?: = (.+))?", line)
        if match:
            defaults = {"integer": "0", "real": "0.0", "boolean": "False"}
            value = _expression(match[3]) if match[3] else defaults.get(match[1], "None")
            statement = f"{match[2]} = {value}"
        elif line.startswith("set "):
            statement = _expression(line[4:])
        elif line.startswith("call "):
            statement = _expression(line[5:])
        elif line == "return" or line.startswith("return "):
            statement = _expression(line)
        else:
            raise ValueError(f"Unsupported JASS line {line_number}: {line}")
        output.append("    " * indent + statement)
    if indent or in_function or in_globals:
        raise ValueError("Unbalanced JASS source")
    return "\n".join(output) + "\n"


class JassRuntime:
    def __init__(self, path: Path | None = None, native_overrides: dict | None = None):
        self.source_path = path or Path(__file__).parents[1] / "runtime" / "kklc_runtime.j"
        self.tables: list[dict] = []
        self.messages: list[str] = []
        self.ns: dict = {
            "InitHashtable": self.init_table,
            "FlushChildHashtable": self.flush,
            "GetHandleId": lambda value: 0 if value is None else value.id,
            "BJDebugMsg": self.messages.append,
            # Explicit scheduling model: action enters synchronously. A test can
            # retain a continuation to represent yielding after that entry.
            "TriggerExecute": lambda target: target.action() if target.action else None,
            "TriggerEvaluate": lambda target: target.condition() if target.condition else True,
        }
        for type_, default in [("Integer", 0), ("Boolean", False), ("Real", 0.0), ("Str", ""), ("UnitHandle", None)]:
            self.ns["Save" + type_] = self._save(type_)
            self.ns["Load" + type_] = self._load(type_, default)
            self.ns["HaveSaved" + type_] = self._have(type_)
            self.ns["RemoveSaved" + type_] = self._remove(type_)
        # Overrides must be active during JASS global initializers too.
        if native_overrides:
            self.ns.update(native_overrides)
        self.python_source = translate_jass(self.source_path.read_text(encoding="utf-8-sig"))
        exec(compile(self.python_source, str(self.source_path), "exec"), self.ns)

    def __getattr__(self, name):
        if name.startswith("KKLC_"):
            return self.ns[name]
        raise AttributeError(name)

    def init_table(self):
        result = {}
        self.tables.append(result)
        return result

    @staticmethod
    def flush(table, parent):
        table.pop(parent, None)

    @staticmethod
    def _save(type_):
        def save(table, parent, child, value):
            if type_ == "Integer":
                value = ((value + 2**31) % 2**32) - 2**31
            table.setdefault(parent, {})[type_, child] = value
            return True
        return save

    @staticmethod
    def _load(type_, default):
        return lambda table, parent, child: table.get(parent, {}).get((type_, child), default)

    @staticmethod
    def _have(type_):
        return lambda table, parent, child: (type_, child) in table.get(parent, {})

    @staticmethod
    def _remove(type_):
        def remove(table, parent, child):
            row = table.get(parent)
            if row is not None:
                row.pop((type_, child), None)
                if not row:
                    table.pop(parent, None)
        return remove

    def save(self, table_name, parent, child, value):
        self.ns["SaveInteger"](self.ns[table_name], parent, child, value)

    def load(self, table_name, parent, child):
        return self.ns["LoadInteger"](self.ns[table_name], parent, child)

    def refs(self, ctx):
        return self.load("KKLC_Meta", ctx, 1)
