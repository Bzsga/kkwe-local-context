#!/usr/bin/env python3
"""KKWE local-context compiler adapter and maintenance entry point.

Only .j/.i/.jass/.out text files are transformed. KKWE extracts its generated
JASS to logs/file.out before invoking Wave. No command in this module opens,
saves or launches a Warcraft map/editor. Installation is delegated to Manager.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import uuid

from paths import resolve_editor_root

ROOT = Path(__file__).resolve().parent
CLI_SOURCE = Path(__file__).read_bytes()
MARKER = "// KKLC_ADAPTED_V1 "
SCRIPT_SUFFIXES = {".j", ".i", ".jass", ".out"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_baseline(editor: Path) -> dict:
    from manager import compiler_pipeline

    spec = json.loads((ROOT / "compatibility.json").read_text(encoding="utf-8"))
    mismatches = []
    for relative, expected in spec["relative_files"].items():
        if isinstance(expected, dict):
            expected = expected["sha256"]
        path = editor / relative
        actual = digest(path.read_bytes()) if path.is_file() else None
        if actual != expected:
            mismatches.append({"file": relative, "expected": expected, "actual": actual})
    pipeline = compiler_pipeline(editor)
    return {"compatible": not mismatches and pipeline["compatible"],
            "mismatches": mismatches, "pipeline": pipeline}


def require_baseline(editor: Path) -> None:
    baseline = check_baseline(editor)
    if not baseline["compatible"]:
        raise ValueError("KKWE 核心或编译配置不兼容，停止处理：" +
                         json.dumps(baseline, ensure_ascii=False))


def read_script(path: Path) -> tuple[str, str, bool]:
    return decode_script(path.read_bytes(), path)


def decode_script(data: bytes, path: Path) -> tuple[str, str, bool]:
    if b"\0" in data or data.startswith((b"HM3W", b"MPQ\x1a", b"MPQ\x1b", b"MZ")):
        raise ValueError("检测到地图或二进制内容，不是 JASS 文本，拒绝处理：" + str(path))
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        return data.decode("utf-8-sig"), "utf-8", bom
    except UnicodeDecodeError:
        return data.decode("gb18030"), "gb18030", False


def require_unchanged(path: Path, expected: bytes | None) -> None:
    current = path.read_bytes() if path.exists() else None
    if current != expected:
        raise ValueError("编译期间文件发生变化，停止覆盖；请避免同时保存或更新工具后重试：" + str(path))


def load_source_snapshot(filename: str):
    """Execute the exact trusted source bytes used in the output identity.

    A cached module/pyc can otherwise execute old code under a new disk hash.
    The temporary module entry is needed by dataclasses during definition.
    """
    path = ROOT / filename
    source = path.read_bytes()
    name = "_kklc_snapshot_" + uuid.uuid4().hex
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module, source


def load_adapter_snapshot():
    return load_source_snapshot("adapter.py")


def compiler_identity(adapter: bytes, runtime: bytes, finalizer: bytes) -> str:
    return digest(b"KKLC_PIPELINE_V3\0" + b"".join(
        len(part).to_bytes(8, "big") + part for part in (adapter, runtime, finalizer, CLI_SOURCE)))


def atomic_write(path: Path, data: bytes, expected: bytes | None, guards=()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".kklc-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        for guard_path, guard_bytes in guards:
            require_unchanged(guard_path, guard_bytes)
        require_unchanged(path, expected)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def add_runtime_dependencies(source: str, adapter) -> str:
    """Keep vJASS/Zinc libraries after the generated runtime in JassHelper."""
    tokens = adapter.lex(source)
    edits = []
    for index, token in enumerate(tokens):
        if token.kind != 'id' or token.value not in {'library', 'library_once'}:
            continue
        if index and source[source.rfind('\n', 0, token.start) + 1:token.start].strip():
            continue
        header = []
        for next_index in range(index + 1, len(tokens)):
            following = tokens[next_index]
            if following.value == '{' or '\n' in source[(header[-1] if header else token).end:following.start]:
                break
            header.append(following)
        if not header:
            raise ValueError('Incomplete library header in adapted script')
        if any(t.value == 'KKLCRuntime' for t in header):
            continue
        dependency = any(t.value in {'requires', 'needs', 'uses'} for t in header)
        edits.append((header[-1].end, ', KKLCRuntime' if dependency else ' requires KKLCRuntime'))
    for position, insertion in reversed(edits):
        source = source[:position] + insertion + source[position:]
    return source


def with_runtime_library(text: str, prefix_length: int, runtime: str, adapter) -> str:
    """Initialize runtime readiness before dependent user libraries run."""
    generated, source = text[:prefix_length], text[prefix_length:]
    if any(token.kind == 'id' and token.value == 'KKLCRuntime' for token in adapter.lex(source)):
        raise ValueError('Input contains reserved generated library name KKLCRuntime')
    return ('library KKLCRuntime initializer KKLC_Init\n' + runtime + '\n' + generated + '\nendlibrary\n'
            + add_runtime_dependencies(source, adapter))


def transform_file(source: Path, output: Path | None, editor: Path) -> dict:
    source = source.resolve(strict=True)
    output = output.resolve() if output else source
    if source.suffix.lower() not in SCRIPT_SUFFIXES or output.suffix.lower() not in SCRIPT_SUFFIXES:
        raise ValueError("只允许转换 .j/.i/.jass/.out 文本脚本；本工具不写地图。")
    source_bytes = source.read_bytes()
    text, encoding, bom = decode_script(source_bytes, source)
    output_bytes = source_bytes if output == source else (output.read_bytes() if output.exists() else None)
    if output != source and output.exists():
        decode_script(output_bytes, output)  # Do not overwrite a renamed binary.
    adapter, adapter_bytes = load_adapter_snapshot()
    _, finalizer_bytes = load_source_snapshot("final_output.py")
    runtime = (ROOT / "runtime" / "kklc_runtime.j").read_bytes()
    identity = compiler_identity(adapter_bytes, runtime, finalizer_bytes)
    guards = ((source, source_bytes), (Path(__file__), CLI_SOURCE), (ROOT / "adapter.py", adapter_bytes),
              (ROOT / "runtime" / "kklc_runtime.j", runtime),
              (ROOT / "final_output.py", finalizer_bytes))
    if text.startswith(MARKER):
        require_baseline(editor)
        first = text.splitlines()[0]
        if first != MARKER + identity:
            raise ValueError("脚本来自其他版本的 KKLC；请从原 GUI 重新生成，不能重复叠加不同版本。")
        if output != source:
            atomic_write(output, source_bytes, output_bytes, guards)
        else:
            for path, expected in guards:
                require_unchanged(path, expected)
        return {"ok": True, "changed": False, "already_adapted": True, "output": str(output)}
    result, metadata = adapter.transform(text)
    changed = result != text
    if changed:
        require_baseline(editor)
        prefix = (
            MARKER + identity + "\n"
            "#if WARCRAFT_VERSION < 124\n#error KKLC_requires_Warcraft_1_24_or_newer\n#endif\n"
            "#include <YDTrigger/SaveLoadSystem/HTSet_Get.h>\n"
            "#include <YDTrigger/SaveLoadSystem/Has_Clear.h>\n"
        )
        result = prefix + with_runtime_library(result, metadata.get('generated_prefix_length', 0),
                                              runtime.decode("utf-8-sig"), adapter)
    if changed or output != source:
        encoded = result.encode(encoding)
        if bom:
            encoded = b"\xef\xbb\xbf" + encoded
        atomic_write(output, encoded, output_bytes, guards)
    else:
        for path, expected in guards:
            require_unchanged(path, expected)
    return {"ok": True, "changed": changed, "output": str(output), "encoding": encoding, "adapter": metadata}


def validate_file(source: Path, editor: Path) -> dict:
    """Validate/finalize actual template output, including deferred includes."""
    adapter, adapter_bytes = load_adapter_snapshot()
    finalizer, finalizer_bytes = load_source_snapshot("final_output.py")
    lex = adapter.lex
    source = source.resolve(strict=True)
    if source.suffix.lower() not in SCRIPT_SUFFIXES:
        raise ValueError("只允许检查 JASS 文本输出。")
    source_bytes = source.read_bytes()
    text, encoding, bom = decode_script(source_bytes, source)
    runtime = (ROOT / "runtime" / "kklc_runtime.j").read_bytes()
    identity = compiler_identity(adapter_bytes, runtime, finalizer_bytes)
    guards = ((source, source_bytes), (Path(__file__), CLI_SOURCE), (ROOT / "adapter.py", adapter_bytes),
              (ROOT / "runtime" / "kklc_runtime.j", runtime),
              (ROOT / "final_output.py", finalizer_bytes))
    markers = [line.strip() for line in text.splitlines() if line.strip().startswith(MARKER)]
    if markers and (len(markers) != 1 or markers[0] != MARKER + identity):
        raise ValueError("最终脚本的 KKLC 版本标记不匹配，请重新生成。")
    if markers and not check_baseline(editor)["compatible"]:
        raise ValueError("编译期间检测到 KKWE 核心变化，停止本次输出。")
    bad = []
    for token in lex(text):
        if token.kind != "id":
            continue
        if token.value == "YDLOC":
            begin = text.rfind("\n", 0, token.start) + 1
            end = text.find("\n", token.end)
            line = text[begin:len(text) if end < 0 else end]
            values = [t.value for t in lex(line)]
            if values in (["hashtable", "YDLOC"], ["gamecache", "YDLOC"],
                          ["set", "YDLOC", "=", "InitHashtable", "(", ")"]):
                continue
            bad.append(token)
        elif token.value.startswith(("YDLocal", "YDLOCAL_", "ydl_localvar_step")):
            bad.append(token)
    if bad:
        details = [{"line": text.count("\n", 0, t.start) + 1, "token": t.value} for t in bad[:12]]
        raise ValueError("包含未适配的旧逆天变量访问（可能来自外部 include/模板或自定义 JASS），停止编译：" + json.dumps(details, ensure_ascii=False))
    result, metadata = finalizer.finalize(text, adapter) if markers else (text, {"changed": False})
    if markers:
        # Includes/templates may introduce additional vJASS or Zinc libraries.
        result = add_runtime_dependencies(result, adapter)
    if result != text:
        encoded = result.encode(encoding)
        if bom:
            encoded = b"\xef\xbb\xbf" + encoded
        atomic_write(source, encoded, source_bytes, guards)
    else:
        for path, expected in guards:
            require_unchanged(path, expected)
    return {"ok": True, "adapted": bool(markers), "input": str(source), "legacy_accesses": 0,
            "finalization": metadata}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "apply", "repair", "remove"):
        command = sub.add_parser(name)
        command.add_argument("--editor-root", type=Path)
    command = sub.add_parser("transform")
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--output", type=Path)
    command.add_argument("--editor-root", type=Path)
    command = sub.add_parser("validate")
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--editor-root", type=Path)
    command = sub.add_parser("verify")
    command.add_argument("--editor-root", type=Path)
    args = parser.parse_args()
    try:
        args.editor_root = resolve_editor_root(args.editor_root, tool_root=ROOT)
        if args.editor_root is None:
            raise ValueError("未配置 KKWE 编辑器目录；请传入 --editor-root、设置 KKLC_EDITOR_ROOT，或先在管理器中选择目录。")
        if args.command == "transform":
            result = transform_file(args.input, args.output, args.editor_root)
        elif args.command == "validate":
            result = validate_file(args.input, args.editor_root)
        elif args.command == "verify":
            from verify import verify
            result = verify(args.editor_root)
        else:
            from manager import Manager
            result = getattr(Manager(args.editor_root), args.command)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in {"status", "apply", "repair", "remove"}:
            if args.command == "remove":
                return 1 if result["state"] in {"error", "conflict"} else 0
            return 0 if result["state"] in {"installed", "original-supported"} else 1
        return 0 if result.get("ok", result.get("compatible", True)) else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
