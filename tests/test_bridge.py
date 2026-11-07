"""Run the Lua bridge with fake processes; never launch an editor or compiler."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bridge_manager', ROOT / 'manager.py')
manager = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(manager)

def find_lua():
    """Use an explicit interpreter or a standard Lua 5.3+ executable on PATH."""
    configured = os.environ.get('KKLC_LUA', '').strip()
    candidates = [configured] if configured else ['lua5.4', 'lua54', 'lua5.3', 'lua53', 'lua']
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable is None and Path(candidate).is_file():
            executable = str(Path(candidate).resolve())
        if executable is None:
            continue
        try:
            probe = subprocess.run([executable, '-e', 'io.write(_VERSION)'],
                                   capture_output=True, encoding='utf-8', errors='replace', timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            continue
        version = re.fullmatch(r'Lua (\d+)\.(\d+)', probe.stdout.strip())
        if probe.returncode == 0 and version and tuple(map(int, version.groups())) >= (5, 3):
            return executable
    return None


LUA = find_lua()


def lua_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


@unittest.skipUnless(LUA, 'Lua 5.3+ is required; set KKLC_LUA or add a standard Lua executable to PATH')
class BridgeIntegrationTests(unittest.TestCase):
    def run_case(self, mode: str):
        with tempfile.TemporaryDirectory(prefix='KKLC Lua 中文 ') as temporary:
            base = Path(temporary)
            editor = base / 'Editor with 空格'
            python = base / 'Python 中文 path' / 'python.exe'
            # Use the actual template while supplying paths that exercise quoting.
            bridge = manager.Manager(editor, ROOT, python_executable=python).bridge()
            input_path = base / '脚本 with spaces.j'
            source = (ROOT / 'tests/fixtures/compiler.lua').read_text(encoding='utf-8')
            anchor = manager.ANCHOR.search(source)
            assert anchor is not None
            patched = source[:anchor.end()] + bridge + source[anchor.end():]
            patched_path = base / 'patched_wave.lua'
            patched_path.write_text(patched, encoding='utf-8')
            output_path = base / 'lua processed 输出.j'
            command_prefix = f'"{python.resolve()}" -X utf8 -B "{ROOT / "cli.py"}"'
            expected_transform = command_prefix + f' transform --input "{input_path}" --editor-root "{editor.resolve()}"'
            expected_validate = command_prefix + f' validate --input "{output_path}" --editor-root "{editor.resolve()}"'
            harness = f'''assert(loadfile({lua_string(patched_path)}))
local mode = {lua_string(mode)}
local captured, errors, body_hits, closed, template_hits = {{}}, {{}}, 0, 0, 0
sys = {{spawn_pipe = function(command, second)
    table.insert(captured, command)
    assert(second == nil)
    local validating = command:find(' validate --input ', 1, true) ~= nil
    if mode == 'spawn_fail' then return nil end
    if mode == 'spawn_error' then error('mock spawn exception') end
    local proc = {{wait = function()
        if mode == 'nonzero' or (mode == 'validate_fail' and validating) then return 17 end
        return 0
    end, close = function() closed = closed + 1 end}}
    local stdout = {{read = function(_, count) assert(count == '*a'); return 'mock stdout' end}}
    local stderr = {{read = function(_, count) assert(count == '*a'); return 'mock stderr' end}}
    return proc, stdout, stderr, {{}}
end}}
log = {{error = function(value) table.insert(errors, value) end}}
gui = {{error_message = function(_, format, value) assert(format == '%s'); table.insert(errors, value) end}}
template = {{}}
local original_template = function(self, op, extra, number)
    assert(self == template and extra == 'extra argument' and number == 42)
    template_hits = template_hits + 1
    if mode == 'template_fail' then return false, 'original failure', nil, 99 end
    op.output = {{string = function() return {lua_string(output_path)} end}}
    return true, 'original success', nil, 99
end
template.compile = original_template
if mode == 'template_missing' then template = nil end
wave = {{}}
function wave:compile(op)
{bridge}
    body_hits = body_hits + 1
    return 'original body reached'
end
local op = {{input = {{string = function() return {lua_string(input_path)} end}}}}
local result = wave:compile(op)
local transform_failed = mode == 'spawn_fail' or mode == 'spawn_error' or mode == 'nonzero' or mode == 'template_missing'
if transform_failed then
    assert(result == false, 'failure must abort wave compilation')
    assert(body_hits == 0 and #errors == 2 and template_hits == 0)
    if mode == 'template_missing' then assert(#captured == 0)
    else assert(captured[1] == {lua_string(expected_transform)}) end
else
    assert(result == 'original body reached' and body_hits == 1 and #errors == 0)
    assert(captured[1] == {lua_string(expected_transform)}, 'Windows transform command quoting changed')
    local wrapper = template.compile
    assert(wrapper ~= original_template)
    if mode == 'repeat' then
        assert(wave:compile(op) == 'original body reached')
        assert(template.compile == wrapper, 'template compiler must not be wrapped twice')
        assert(body_hits == 2)
    end
    local results = table.pack(template:compile(op, 'extra argument', 42))
    assert(template_hits == 1, 'original template compiler must run exactly once')
    if mode == 'template_fail' then
        assert(results.n == 4 and results[1] == false and results[2] == 'original failure' and results[3] == nil and results[4] == 99)
        assert(#captured == 1 and #errors == 0, 'failed template must not be validated')
    else
        assert(captured[#captured] == {lua_string(expected_validate)}, 'Windows validation command quoting changed')
        assert(#captured == (mode == 'repeat' and 3 or 2))
        if mode == 'validate_fail' then
            assert(results.n == 1 and results[1] == false and #errors == 2)
        else
            assert(results.n == 4 and results[1] == true and results[2] == 'original success' and results[3] == nil and results[4] == 99)
            assert(#errors == 0)
        end
    end
end
print('PASS:' .. mode)
'''
            harness_path = base / 'bridge_test.lua'
            harness_path.write_text(harness, encoding='utf-8')
            completed = subprocess.run([str(LUA), str(harness_path)], capture_output=True,
                                       encoding='utf-8', errors='replace', timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stdout + '\n' + completed.stderr)
            self.assertIn('PASS:' + mode, completed.stdout)

    def test_success_preserves_original_body_and_unicode_command(self):
        self.run_case('success')

    def test_spawn_failure_prevents_compilation(self):
        self.run_case('spawn_fail')

    def test_spawn_exception_prevents_compilation(self):
        self.run_case('spawn_error')

    def test_nonzero_exit_prevents_compilation(self):
        self.run_case('nonzero')

    def test_final_validation_failure_stops_compilation(self):
        self.run_case('validate_fail')

    def test_original_template_failure_skips_validation(self):
        self.run_case('template_fail')

    def test_repeated_wave_does_not_duplicate_wrapper(self):
        self.run_case('repeat')

    def test_missing_template_compiler_fails_explicitly(self):
        self.run_case('template_missing')


if __name__ == '__main__':
    unittest.main()
