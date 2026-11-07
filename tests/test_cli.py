from pathlib import Path
import contextlib
import io
import tempfile
import shutil
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli


class CLITests(unittest.TestCase):
    LOCAL_SOURCE = b'function A takes nothing returns nothing\nYDLocalInitialize()\ncall YDLocal1Set(integer,"x",1)\nendfunction\n'

    def test_concurrent_source_change_is_not_overwritten(self):
        for separate in (False, True):
            with self.subTest(separate=separate), tempfile.TemporaryDirectory() as d:
                source = Path(d) / "file.out"
                output = Path(d) / "output.j" if separate else source
                source.write_bytes(self.LOCAL_SOURCE)
                if separate:
                    output.write_bytes(b"// preserved output")
                before = output.read_bytes()
                with patch.object(cli, "require_baseline", side_effect=lambda _: source.write_bytes(b"// newer save")):
                    with self.assertRaisesRegex(ValueError, "文件发生变化"):
                        cli.transform_file(source, output, Path(d))
                self.assertEqual(source.read_bytes(), b"// newer save")
                if separate:
                    self.assertEqual(output.read_bytes(), before)
                self.assertEqual(list(Path(d).glob("*.tmp")), [])

    def test_concurrent_output_change_or_creation_is_not_overwritten(self):
        for exists in (False, True):
            with self.subTest(exists=exists), tempfile.TemporaryDirectory() as d:
                source, output = Path(d) / "source.j", Path(d) / "file.out"
                source.write_bytes(self.LOCAL_SOURCE)
                if exists:
                    output.write_bytes(b"// old output")
                with patch.object(cli, "require_baseline", side_effect=lambda _: output.write_bytes(b"// concurrent output")):
                    with self.assertRaisesRegex(ValueError, "文件发生变化"):
                        cli.transform_file(source, output, Path(d))
                self.assertEqual(output.read_bytes(), b"// concurrent output")
                self.assertEqual(source.read_bytes(), self.LOCAL_SOURCE)

    def test_transform_executes_exact_source_snapshot_not_cached_import(self):
        import adapter  # Deliberately leave the earlier version in sys.modules.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "runtime").mkdir()
            shutil.copy2(cli.ROOT / "runtime/kklc_runtime.j", root / "runtime/kklc_runtime.j")
            shutil.copy2(cli.ROOT / "final_output.py", root / "final_output.py")
            adapter_bytes = (cli.ROOT / "adapter.py").read_bytes()
            adapter_bytes += b'\n_old_transform = transform\ndef transform(text):\n    result, meta = _old_transform(text)\n    return result + "\\n// loaded-current-source\\n", meta\n'
            (root / "adapter.py").write_bytes(adapter_bytes)
            source = root / "file.out"
            source.write_bytes(self.LOCAL_SOURCE)
            with patch.object(cli, "ROOT", root), patch.object(cli, "check_baseline", return_value={"compatible": True}), patch.object(adapter, "transform", side_effect=AssertionError("cached adapter executed")):
                cli.transform_file(source, None, root)
                text = source.read_text(encoding="utf-8")
                self.assertIn("// loaded-current-source", text)
                self.assertEqual(text.splitlines()[0], cli.MARKER + cli.compiler_identity(adapter_bytes, (root / "runtime/kklc_runtime.j").read_bytes(), (root / "final_output.py").read_bytes()))
                self.assertTrue(cli.validate_file(source, root)["ok"])

    def test_tool_update_during_transform_stops_before_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            shutil.copy2(cli.ROOT / "adapter.py", root / "adapter.py")
            shutil.copy2(cli.ROOT / "final_output.py", root / "final_output.py")
            shutil.copytree(cli.ROOT / "runtime", root / "runtime")
            source = root / "file.out"
            source.write_bytes(self.LOCAL_SOURCE)
            with patch.object(cli, "ROOT", root), patch.object(cli, "require_baseline", side_effect=lambda _: (root / "adapter.py").write_bytes(b"# newer tool")):
                with self.assertRaisesRegex(ValueError, "文件发生变化"):
                    cli.transform_file(source, None, root)
            self.assertEqual(source.read_bytes(), self.LOCAL_SOURCE)

    def test_finalization_wraps_late_destruction_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d, patch.object(cli, "check_baseline", return_value={"compatible": True}):
            source = Path(d) / "file.out"
            source.write_bytes(b"\xef\xbb\xbf" + self.LOCAL_SOURCE)
            cli.transform_file(source, None, Path(d))
            source.write_bytes(source.read_bytes() + b'\nfunction Late takes nothing returns nothing\ncall DestroyTimer(CreateTimer())\ncall DestroyTrigger(CreateTrigger())\nendfunction\n')
            report = cli.validate_file(source, Path(d))
            self.assertEqual(report["finalization"]["destroy_calls_wrapped"], {"DestroyTimer": 1, "DestroyTrigger": 1})
            first = source.read_bytes()
            self.assertTrue(first.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"call KKLC_DestroyTimer(CreateTimer())", first)
            self.assertFalse(cli.validate_file(source, Path(d))["finalization"]["changed"])
            self.assertEqual(source.read_bytes(), first)

    def test_finalization_rejects_late_helper_entry_without_partial_write(self):
        with tempfile.TemporaryDirectory() as d, patch.object(cli, "check_baseline", return_value={"compatible": True}):
            source = Path(d) / "file.out"
            source.write_bytes(b'function Helper takes nothing returns nothing\ncall YDLocal2Set(integer,"h",1)\nendfunction\n' + self.LOCAL_SOURCE.replace(b'call YDLocal1Set', b'call Helper()\ncall YDLocal1Set'))
            cli.transform_file(source, None, Path(d))
            source.write_bytes(source.read_bytes() + b'\nfunction Late takes nothing returns nothing\ncall DestroyTimer(CreateTimer())\ncall ExecuteFunc("Helper")\nendfunction\n')
            before = source.read_bytes()
            with self.assertRaisesRegex(ValueError, "helper context"):
                cli.validate_file(source, Path(d))
            self.assertEqual(source.read_bytes(), before)

    def test_finalization_does_not_overwrite_concurrent_template_output(self):
        with tempfile.TemporaryDirectory() as d, patch.object(cli, "check_baseline", return_value={"compatible": True}):
            source = Path(d) / "file.out"
            source.write_bytes(self.LOCAL_SOURCE)
            cli.transform_file(source, None, Path(d))
            source.write_bytes(source.read_bytes() + b'\nfunction Late takes nothing returns nothing\ncall DestroyTimer(CreateTimer())\nendfunction\n')
            def replace_output(_):
                source.write_bytes(b"// another template output")
                return {"compatible": True}
            with patch.object(cli, "check_baseline", side_effect=replace_output):
                with self.assertRaisesRegex(ValueError, "文件发生变化"):
                    cli.validate_file(source, Path(d))
            self.assertEqual(source.read_bytes(), b"// another template output")

    def test_editor_extracted_out_input_transforms_in_place(self):
        with tempfile.TemporaryDirectory() as d, patch.object(cli, "check_baseline", return_value={"compatible": True}):
            path = Path(d) / "file.out"
            path.write_text('function A takes nothing returns nothing\nYDLocalInitialize()\ncall YDLocal1Set(integer,"x",1)\nendfunction\n', encoding="utf-8")
            self.assertTrue(cli.transform_file(path, None, Path(d))["changed"])
            first = path.read_bytes()
            self.assertTrue(first.startswith(cli.MARKER.encode()))
            self.assertTrue(cli.transform_file(path, None, Path(d))["already_adapted"])
            self.assertEqual(first, path.read_bytes())

    def test_plain_out_preserves_bom_and_newlines(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "FILE.OUT"
            data = b'\xef\xbb\xbffunction main takes nothing returns nothing\r\nendfunction\r\n'
            path.write_bytes(data)
            self.assertFalse(cli.transform_file(path, None, Path(d))["changed"])
            self.assertTrue(cli.validate_file(path, Path(d))["ok"])
            self.assertEqual(data, path.read_bytes())

    def test_binary_disguised_as_script_is_not_transformed_or_validated(self):
        for suffix in (".out", ".j"):
            for data in (b'HM3Wfake', b'MPQ\x1afake', b'MPQ\x1bfake', b'MZfake', b'text\0binary'):
                with self.subTest(suffix=suffix, data=data), tempfile.TemporaryDirectory() as d:
                    path = Path(d) / ("renamed" + suffix)
                    path.write_bytes(data)
                    with self.assertRaisesRegex(ValueError, "二进制"):
                        cli.transform_file(path, None, Path(d))
                    with self.assertRaisesRegex(ValueError, "二进制"):
                        cli.validate_file(path, Path(d))
                    self.assertEqual(data, path.read_bytes())

    def test_existing_binary_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d) / "source.j", Path(d) / "file.out"
            source.write_text('function A takes nothing returns nothing\nendfunction\n', encoding="utf-8")
            target.write_bytes(b'HM3Wfake')
            with self.assertRaisesRegex(ValueError, "二进制"):
                cli.transform_file(source, target, Path(d))
            self.assertEqual(target.read_bytes(), b'HM3Wfake')

    def test_manager_exit_code_reports_action_outcome(self):
        cases = [
            ("apply", {"state": "dependency-missing", "compatible": True}, 1),
            ("remove", {"state": "upstream-changed", "compatible": False, "action": "removed"}, 0),
        ]
        for command, result, expected in cases:
            with self.subTest(command=command), patch('manager.Manager') as manager:
                getattr(manager.return_value, command).return_value = result
                with patch.object(sys, 'argv', ['cli.py', command, '--editor-root', str(Path.cwd())]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(), expected)

    def test_map_extensions_are_never_written(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "input.j"
            path.write_bytes(b"original")
            with self.assertRaises(ValueError):
                cli.transform_file(path, Path(d) / "map.w3x", Path(d))
            self.assertEqual(path.read_bytes(), b"original")
            self.assertFalse((Path(d) / "map.w3x").exists())

    def test_baseline_includes_active_compiler_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "compatibility.json").write_text('{"relative_files": {}}', encoding="utf-8")
            (root / "bin").mkdir()
            config = root / "bin" / "EverConfig.cfg"
            config.write_text('[ScriptCompiler]\nEnableCJass=1\nEnableJassHelper=1\n', encoding="utf-8")
            with patch.object(cli, "ROOT", root):
                result = cli.check_baseline(root)
                self.assertFalse(result["compatible"])
                self.assertEqual(result["mismatches"], [])
                with self.assertRaisesRegex(ValueError, "EnableCJass"):
                    cli.require_baseline(root)
                config.write_text('[ScriptCompiler]\nEnableCJass=0\nEnableJassHelper=1\n', encoding="utf-8")
                self.assertTrue(cli.check_baseline(root)["compatible"])

    def test_no_local_input_preserved_even_with_unknown_editor(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plain.j"
            data = b"\xef\xbb\xbffunction main takes nothing returns nothing\r\nendfunction\r\n"
            path.write_bytes(data)
            result = cli.transform_file(path, None, Path(d))
            self.assertFalse(result["changed"])
            self.assertEqual(path.read_bytes(), data)

    def test_unknown_baseline_does_not_overwrite_local_script(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "local.j"
            data = b'function A takes nothing returns nothing\nYDLocalInitialize()\ncall YDLocal1Set(integer,"x",1)\nendfunction\n'
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                cli.transform_file(path, None, Path(d))
            self.assertEqual(path.read_bytes(), data)

    def test_validation_permits_only_unused_legacy_table_declaration(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.j"
            path.write_text('globals\nhashtable YDLOC\nendglobals\nfunction Init takes nothing returns nothing\nset YDLOC=InitHashtable()\ncall BJDebugMsg("YDLOC and YDLocal1Get")\nendfunction\n', encoding="utf-8")
            self.assertTrue(cli.validate_file(path, Path(d))["ok"])
            path.write_text('function X takes nothing returns nothing\ncall SaveInteger(YDLOC, 1, 2, 3)\nendfunction', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "include"):
                cli.validate_file(path, Path(d))

    def test_validation_rejects_deferred_macro(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.j"
            path.write_text('call YDLocal1Set(integer,"x",1)', encoding="utf-8")
            with self.assertRaises(ValueError):
                cli.validate_file(path, Path(d))

    def test_encoding_and_idempotence(self):
        with tempfile.TemporaryDirectory() as d, patch.object(cli, "check_baseline", return_value={"compatible": True}):
            path = Path(d) / "gbk.j"
            path.write_bytes('// 中文\r\nfunction A takes nothing returns nothing\r\nYDLocalInitialize()\r\ncall YDLocal1Set(integer,"数值",1)\r\nendfunction\r\n'.encode("gb18030"))
            result = cli.transform_file(path, None, Path(d))
            self.assertEqual(result["encoding"], "gb18030")
            first = path.read_bytes()
            self.assertIn("中文", first.decode("gb18030"))
            self.assertTrue(cli.transform_file(path, None, Path(d))["already_adapted"])
            self.assertEqual(first, path.read_bytes())
