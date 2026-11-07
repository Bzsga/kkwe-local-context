"""Offline source-execution tests and real KKWE script-only compiler checks."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parent


def _run(command: list[str], cwd: Path, timeout: int = 60) -> dict:
    result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
    def decode(value):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("gb18030", errors="replace")
    return {"command": command, "exit_code": result.returncode,
            "stdout": decode(result.stdout), "stderr": decode(result.stderr),
            "ok": result.returncode == 0}


def compile_script(source: Path, editor: Path, run: Path) -> dict:
    """Use trusted installed executables in an isolated working directory."""
    from cli import transform_file, validate_file
    run.mkdir(parents=True, exist_ok=True)
    jh = editor / "plugin" / "jasshelper"
    for name in ("clijasshelper.exe", "SFmpq.dll", "StormLib.dll", "pjass.exe"):
        shutil.copy2(jh / name, run / name)
    shutil.copytree(jh / "bin", run / "bin", dirs_exist_ok=True)
    (run / "logs").mkdir(exist_ok=True)
    (run / "jasshelper.conf").write_text('[jasscompiler]\n"pjass.exe"\n"$COMMONJ $BLIZZARDJ $WAR3MAPJ"\n', encoding="utf-8")
    transformed = run / "adapted.j"
    adaptation = transform_file(source, transformed, editor)
    wave = editor / "plugin" / "wave"
    command = [str(wave / "Wave.exe"), "--autooutput",
               "--sysinclude=" + str(wave / "include"),
               "--sysinclude=" + str(editor / "plugin"),
               "--include=" + str(editor / "jass"),
               "--define=WARCRAFT_VERSION=126", "--extended", "--c99",
               "--preserve=2", "--line=0", str(transformed)]
    preprocessing = _run(command, run)
    result = {"source": str(source), "adaptation": adaptation, "wave": preprocessing, "ok": False}
    if not preprocessing["ok"]:
        return result
    preprocessed = transformed.with_suffix(".i")
    if not preprocessed.exists():
        result["error"] = "Wave did not produce .i"
        return result
    # The test corpus uses no Lua templates. Production retains the editor's
    # original template phase; do not silently emulate arbitrary Lua here.
    if "<?" in preprocessed.read_text(encoding="utf-8-sig", errors="replace"):
        result["error"] = "Offline fixture contains Lua templates; use the real template stage"
        return result
    result["final_validation"] = validate_file(preprocessed, editor)
    compiled = run / "compiled.j"
    compilation = _run([str(run / "clijasshelper.exe"), "--nooptimize", "--scriptonly",
                        str(jh / "common.j"), str(jh / "blizzard.j"),
                        str(preprocessed), str(compiled)], run)
    result["jasshelper"] = compilation
    result["ok"] = compilation["ok"] and compiled.is_file()
    if result["ok"]:
        result["pjass"] = _run([str(run / "pjass.exe"), str(jh / "common.j"),
                                 str(jh / "blizzard.j"), str(compiled)], run)
        result["ok"] = result["pjass"]["ok"]
    for f in (run / "logs").glob("*.txt"):
        if "error" in f.name.lower():
            result[f.name] = f.read_text(encoding="gb18030", errors="replace")
    return result


def verify(editor: Path) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = ROOT / "runs" / ("offline-" + stamp)
    run.mkdir(parents=True, exist_ok=True)
    unit = _run([sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], ROOT)
    compilation = compile_script(ROOT / "verification" / "integration.j", editor, run / "integration")
    game_candidate = compile_script(ROOT / "verification" / "game_test.j", editor, run / "game_candidate")
    result = {"ok": unit["ok"] and compilation["ok"] and game_candidate["ok"], "unit": unit,
              "compile": compilation, "game_candidate_compile": game_candidate, "game_tested": False,
              "boundary": "Actual JASS source executed with native mocks; actual Wave/JassHelper/PJASS. No map written or game/editor launched.",
              "report": str(run / "result.json")}
    (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
