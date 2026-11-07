from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe') if sys.platform == 'win32' else None
spec = importlib.util.spec_from_file_location('kkwe_context_manager', ROOT / 'manager.py')
manager = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(manager)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='KKWE 中文 path ')
        # Windows temp paths may use an 8.3 alias; match Manager's canonical paths.
        self.base = Path(self.temporary.name).resolve()
        self.editor = self.base / 'Editor 中文'
        self.tool = self.base / 'tools 中文'
        shutil.copytree(ROOT / 'bootstrap', self.tool / 'bootstrap')
        (self.tool / 'cli.py').write_text('raise SystemExit(0)\n', encoding='utf-8')
        self.original = (ROOT / 'tests/fixtures/compiler.lua').read_bytes()
        # Keep the synthetic stub out of production's trusted compiler list.
        # Only this temporary tool accepts its test-specific byte/token profile.
        (self.tool / 'bootstrap/compatibility.json').write_text(json.dumps({
            'format': 1, 'profiles': [{
                'id': 'synthetic-test-only',
                'sha256': manager.digest(self.original),
                'semantic_sha256': manager.semantic_digest(self.original.decode('utf-8')),
            }]}), encoding='utf-8')
        self.target = self.editor / manager.TARGET
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.original)
        self.generator = self.editor / 'plugin/YDTrigger.dll'
        self.generator.parent.mkdir(parents=True)
        self.generator.write_bytes(b'fixture generator, never executed')
        (self.tool / 'compatibility.json').write_text(json.dumps({'relative_files': {
            'plugin/YDTrigger.dll': manager.digest(self.generator.read_bytes())}}), encoding='utf-8')
        self.mgr = manager.Manager(self.editor, self.tool)

    def tearDown(self):
        self.temporary.cleanup()

    def test_apply_idempotent_and_exact_remove(self):
        self.assertEqual(self.mgr.status()['state'], 'original-supported')
        applied = self.mgr.apply()
        self.assertEqual(applied['state'], 'installed')
        self.assertTrue(applied['changed'])
        self.assertIn(b"kklc_run_cli('transform', op.input)", self.target.read_bytes())
        self.assertIn(str(self.editor).replace('\\', '\\\\'), self.target.read_text(encoding='utf-8'))
        first = self.target.read_bytes()
        self.assertFalse(self.mgr.apply()['changed'])
        self.assertEqual(first, self.target.read_bytes())
        backups = list((self.tool / 'state/backups').glob('*.wave.lua'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertTrue(self.mgr.remove()['changed'])
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.mgr.remove()['changed'])

    def test_compatible_update_and_version_change_repair(self):
        self.mgr.apply()
        # Simulate an updater replacing wave.lua and a new editor executable.
        updated = self.original + b'\r\n-- updater version 999, user comment\r\n'
        self.target.write_bytes(updated)
        (self.editor / 'KKWE.exe').write_bytes(b'new version, never executed')
        status = self.mgr.status()
        self.assertEqual(status['state'], 'original-supported')
        self.assertEqual(status['support']['verification'], 'same-lua-tokens')
        self.assertEqual(self.mgr.repair()['state'], 'installed')
        self.mgr.remove()
        self.assertEqual(self.target.read_bytes(), updated)

    def test_unknown_code_update_is_preserved(self):
        self.mgr.apply()
        unknown = self.original.replace(b'local exit_code, out, err = self:do_compile(op)',
                                        b'local exit_code, out, err = self:new_compile(op)')
        self.target.write_bytes(unknown)
        self.assertEqual(self.mgr.status()['state'], 'upstream-changed')
        self.assertFalse(self.mgr.repair()['changed'])
        self.assertEqual(self.target.read_bytes(), unknown)

    def test_changed_generator_blocks_repair_and_preserves_source(self):
        self.mgr.apply()
        self.target.write_bytes(self.original)
        self.generator.write_bytes(b'unknown new generator')
        status = self.mgr.status()
        self.assertEqual(status['state'], 'upstream-changed')
        self.assertFalse(status['compatible'])
        self.assertFalse(self.mgr.repair()['changed'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_generator_change_still_allows_exact_bridge_removal(self):
        self.mgr.apply()
        self.generator.write_bytes(b'new generator')
        self.assertEqual(self.mgr.remove()['state'], 'upstream-changed')
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_missing_contract_profile_does_not_install(self):
        (self.tool / 'compatibility.json').unlink()
        self.assertEqual(self.mgr.apply()['state'], 'upstream-changed')
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_cjass_pipeline_is_refused_without_changing_utf8_config(self):
        self.assertTrue(self.mgr.status()['compatible'])  # Missing fields use defaults.
        config = self.editor / 'bin/EverConfig.cfg'
        config.parent.mkdir()
        raw = '; 中文编译设置\n[ScriptCompiler]\nEnableCJass=1\n'.encode('utf-8-sig')
        config.write_bytes(raw)
        status = self.mgr.apply()
        self.assertEqual(status['state'], 'unsupported-pipeline')
        self.assertFalse(status['compatible'])
        self.assertFalse(status['changed'])
        self.assertEqual(status['pipeline']['options']['EnableJassHelper'], '1')
        self.assertEqual(config.read_bytes(), raw)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_disabled_jasshelper_is_refused_without_changing_gb_config(self):
        config = self.editor / 'bin/EverConfig.cfg'
        config.parent.mkdir()
        raw = '; 中文编译设置\r\n[ScriptCompiler]\r\nEnableJassHelper=0\r\n'.encode('gb18030')
        config.write_bytes(raw)
        status = self.mgr.repair()
        self.assertEqual(status['state'], 'unsupported-pipeline')
        self.assertFalse(status['compatible'])
        self.assertFalse(status['changed'])
        self.assertEqual(status['pipeline']['options']['EnableCJass'], '0')
        self.assertEqual(config.read_bytes(), raw)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_refresh_known_bridge_to_new_python_path(self):
        self.mgr.apply()
        new_python = self.base / 'new Python 路径' / 'python.exe'
        new_python.parent.mkdir()
        new_python.write_bytes(b'fixture only')
        newer = manager.Manager(self.editor, self.tool, python_executable=new_python)
        self.assertEqual(newer.repair()['action'], 'bridge-refreshed')
        self.assertEqual(newer.status()['state'], 'installed')
        newer.remove()
        self.assertEqual(self.target.read_bytes(), self.original)

    def _upgrade_bridge_template(self):
        self.mgr.apply()
        before = self.target.read_bytes()
        template = self.tool / 'bootstrap/bridge.lua.in'
        template.write_text(template.read_text(encoding='utf-8').replace(
            '\tdo\n', '\tdo\n\t\t-- next reviewed bridge revision\n', 1), encoding='utf-8')
        return before

    def _assert_refresh_failure_recoverable(self, destination):
        before = self._upgrade_bridge_template()
        write = manager.atomic_write
        def fail_selected(path, data, expected=None):
            if path == destination:
                raise PermissionError('injected write failure')
            return write(path, data, expected)
        with patch.object(manager, 'atomic_write', side_effect=fail_selected):
            with self.assertRaises(PermissionError):
                self.mgr.repair()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.mgr.status()['state'], 'installed')
        self.assertEqual(self.mgr.repair()['action'], 'bridge-refreshed')
        self.assertTrue(self.mgr.remove()['changed'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_failed_target_refresh_keeps_previous_bridge_recoverable(self):
        self._assert_refresh_failure_recoverable(self.target)

    def test_failed_manifest_write_keeps_previous_bridge_recoverable(self):
        self._assert_refresh_failure_recoverable(self.mgr.manifest_path)

    def test_failed_launcher_write_keeps_previous_bridge_recoverable(self):
        self._assert_refresh_failure_recoverable(self.tool / 'state/launcher.json')

    def test_failed_refresh_can_be_removed_without_retry(self):
        before = self._upgrade_bridge_template()
        write = manager.atomic_write
        def fail_target(path, data, expected=None):
            if path == self.target:
                raise PermissionError('injected target replacement failure')
            return write(path, data, expected)
        with patch.object(manager, 'atomic_write', side_effect=fail_target):
            with self.assertRaises(PermissionError):
                self.mgr.repair()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertTrue(self.mgr.remove()['changed'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_refresh_preserves_concurrent_user_change(self):
        before = self._upgrade_bridge_template()
        user_code = b'\nuser_added_during_refresh = true\n'
        write = manager.atomic_write
        def edit_before_target_write(path, data, expected=None):
            if path == self.target:
                path.write_bytes(before + user_code)
            return write(path, data, expected)
        with patch.object(manager, 'atomic_write', side_effect=edit_before_target_write):
            with self.assertRaisesRegex(RuntimeError, 'Target changed'):
                self.mgr.repair()
        self.assertEqual(self.target.read_bytes(), before + user_code)
        self.assertEqual(self.mgr.status()['state'], 'upstream-changed')
        self.assertFalse(self.mgr.repair()['changed'])
        self.assertTrue(self.mgr.remove()['changed'])
        self.assertEqual(self.target.read_bytes(), self.original + user_code)

    def test_remove_keeps_user_code_changes(self):
        self.mgr.apply()
        changed = self.target.read_bytes() + b'\r\nuser_added_flag = true\r\n'
        self.target.write_bytes(changed)
        self.assertEqual(self.mgr.status()['state'], 'upstream-changed')
        self.assertTrue(self.mgr.remove()['changed'])
        self.assertEqual(self.target.read_bytes(), self.original + b'\r\nuser_added_flag = true\r\n')

    def test_modified_bridge_is_not_overwritten_or_removed(self):
        self.mgr.apply()
        changed = self.target.read_bytes().replace(b"kklc_run_cli('transform', op.input)", b"kklc_run_cli('custom', op.input)")
        self.target.write_bytes(changed)
        for operation in [self.mgr.status, self.mgr.apply, self.mgr.repair, self.mgr.remove]:
            self.assertEqual(operation()['state'], 'conflict')
            self.assertEqual(self.target.read_bytes(), changed)

    def test_partial_marker_is_refused(self):
        changed = self.original + ('\n' + manager.BEGIN + '\n').encode('utf-8')
        self.target.write_bytes(changed)
        self.assertEqual(self.mgr.apply()['state'], 'conflict')
        self.assertEqual(self.target.read_bytes(), changed)

    def test_line_endings_and_bom_are_preserved(self):
        changed = b'\xef\xbb\xbf' + self.original.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        self.target.write_bytes(changed)
        self.assertEqual(self.mgr.apply()['state'], 'installed')
        self.mgr.remove()
        self.assertEqual(self.target.read_bytes(), changed)

    def test_semantic_verification_does_not_ignore_strings(self):
        code = 'print("-- literal") -- comment\n'
        self.assertEqual(manager.semantic_digest(code), manager.semantic_digest('print ( "-- literal" )\n'))
        self.assertNotEqual(manager.semantic_digest(code), manager.semantic_digest('print("-- changed")'))
        self.assertEqual(manager.semantic_digest('x=1 --[[ hidden ]]\ny=2'), manager.semantic_digest('x=1 y=2'))

    def test_semantic_verification_preserves_multicharacter_operators(self):
        for operator in ('...', '..', '==', '~=', '<=', '>=', '//', '<<', '>>', '::'):
            with self.subTest(operator=operator):
                compact = 'left ' + operator + ' right'
                split = 'left ' + ' '.join(operator) + ' right'
                commented = 'left ' + '--[[comment]]'.join(operator) + ' right'
                self.assertNotEqual(manager.semantic_digest(compact), manager.semantic_digest(split))
                self.assertNotEqual(manager.semantic_digest(compact), manager.semantic_digest(commented))
                self.assertEqual(manager.semantic_digest(compact),
                                 manager.semantic_digest('left\n' + operator + '\t right'))

    def test_split_lua_operator_is_not_a_compatible_update(self):
        changed = self.original.replace(b'exit_code ~= 0', b'exit_code ~ = 0')
        self.assertNotEqual(changed, self.original)
        self.target.write_bytes(changed)
        self.assertEqual(self.mgr.status()['state'], 'upstream-changed')
        self.assertFalse(self.mgr.repair()['changed'])
        self.assertEqual(self.target.read_bytes(), changed)

    def test_trusted_fixture_matches_profile_and_existing_manifest(self):
        profile = self.mgr._profile()['profiles'][0]
        self.assertEqual(manager.digest(self.original), profile['sha256'])
        self.assertEqual(manager.semantic_digest(self.original.decode('utf-8')), profile['semantic_sha256'])
        self.mgr.apply()
        # Installed format-1 records never contained a tokenizer fingerprint.
        record = json.loads(self.mgr.manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(record['format'], 1)
        self.assertNotIn('semantic_sha256', record)
        self.assertEqual(manager.Manager(self.editor, self.tool).status()['state'], 'installed')
        self.assertFalse(self.mgr.repair()['changed'])

    def test_atomic_write_refuses_stale_content(self):
        before = self.target.read_bytes()
        with self.assertRaises(RuntimeError):
            manager.atomic_write(self.target, b'new', expected=b'outdated')
        self.assertEqual(self.target.read_bytes(), before)

    def test_launch_settings_use_actual_python(self):
        self.mgr.apply()
        settings = json.loads((self.tool / 'state/launcher.json').read_text(encoding='utf-8'))
        self.assertEqual(settings['python'], str(Path(sys.executable).resolve()))
        self.assertEqual(settings['editor_root'], str(self.editor.resolve()))

    def test_uninstall_disables_preflight_until_explicit_install(self):
        self.mgr.apply()
        installed = self.target.read_bytes()
        removed = self.mgr.remove()
        self.assertFalse(removed['auto_repair'])
        self.assertEqual(self.target.read_bytes(), self.original)
        preference = self.mgr.preference_path.read_bytes()
        for _ in range(2):
            result = self.mgr.preflight()
            self.assertEqual(result['state'], 'disabled')
            self.assertFalse(result['changed'])
            self.assertEqual(self.target.read_bytes(), self.original)
            self.assertEqual(self.mgr.preference_path.read_bytes(), preference)
        explicit = self.mgr.repair()
        self.assertEqual(explicit['state'], 'installed')
        self.assertTrue(explicit['auto_repair'])
        self.assertEqual(self.target.read_bytes(), installed)

    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell is required for launcher integration')
    def test_launcher_preflight_respects_uninstall_in_chinese_space_path(self):
        self.mgr.apply()
        self.mgr.remove()
        shutil.copy2(ROOT / 'manager.py', self.tool / 'manager.py')
        shutil.copy2(ROOT / 'launch.ps1', self.tool / 'launch.ps1')
        command = [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                   '-File', str(self.tool / 'launch.ps1'), '-EditorRoot', str(self.editor),
                   '-Python', sys.executable]
        result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('automatic-repair-disabled', result.stdout)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.mgr.status()['auto_repair'])
        self.assertEqual(self.mgr.apply()['state'], 'installed')
        self.assertTrue(self.mgr.status()['auto_repair'])

    def test_preference_is_per_editor_and_status_does_not_create_it(self):
        self.assertTrue(self.mgr.status()['auto_repair'])
        self.assertFalse(self.mgr.preference_path.exists())
        self.mgr.remove()
        other = manager.Manager(self.base / 'Other Editor', self.tool)
        self.assertTrue(other.status()['auto_repair'])
        self.assertFalse(other.preference_path.exists())
        self.assertNotEqual(other.preference_path, self.mgr.preference_path)

    def test_preference_write_failure_aborts_uninstall(self):
        self.mgr.apply()
        installed = self.target.read_bytes()
        write = manager.atomic_write
        def fail_preference(path, data, expected=None):
            if path == self.mgr.preference_path:
                raise PermissionError('cannot persist uninstall preference')
            return write(path, data, expected)
        with patch.object(manager, 'atomic_write', side_effect=fail_preference):
            with self.assertRaises(PermissionError):
                self.mgr.remove()
        self.assertEqual(self.target.read_bytes(), installed)
        self.assertTrue(self.mgr.status()['auto_repair'])

    def test_conflicting_bridge_uninstall_still_disables_automatic_repair(self):
        self.mgr.apply()
        conflict = self.target.read_bytes().replace(b"kklc_run_cli('transform', op.input)",
                                                  b"kklc_run_cli('other', op.input)")
        self.target.write_bytes(conflict)
        result = self.mgr.remove()
        self.assertEqual(result['state'], 'conflict')
        self.assertFalse(result['auto_repair'])
        self.assertEqual(self.target.read_bytes(), conflict)
        self.assertEqual(self.mgr.preflight()['state'], 'removal-incomplete')
        self.assertEqual(self.target.read_bytes(), conflict)

    def test_failed_target_removal_blocks_old_launcher(self):
        self.mgr.apply()
        installed = self.target.read_bytes()
        write = manager.atomic_write
        def fail_target(path, data, expected=None):
            if path == self.target:
                raise PermissionError('target is occupied')
            return write(path, data, expected)
        with patch.object(manager, 'atomic_write', side_effect=fail_target):
            with self.assertRaises(PermissionError):
                self.mgr.remove()
        self.assertFalse(self.mgr.status()['auto_repair'])
        self.assertEqual(self.mgr.preflight()['state'], 'removal-incomplete')
        self.assertEqual(self.target.read_bytes(), installed)

    def test_disabled_preflight_allows_unknown_unmodified_editor(self):
        self.mgr.remove()
        changed = self.original + b'\nnew_official_compiler_setting = true\n'
        self.target.write_bytes(changed)
        result = self.mgr.preflight()
        self.assertEqual(result['state'], 'disabled')
        self.assertEqual(result['compiler_state'], 'upstream-changed')
        self.assertEqual(self.target.read_bytes(), changed)

    def test_corrupt_preference_blocks_automatic_repair(self):
        self.mgr.apply()
        self.target.write_bytes(self.original)
        self.mgr.preference_path.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.mgr.status()['state'], 'preference-error')
        with self.assertRaises(ValueError):
            self.mgr.preflight()
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_legacy_default_preflight_still_recovers_known_update(self):
        self.assertFalse(self.mgr.preference_path.exists())
        self.assertEqual(self.mgr.preflight()['state'], 'installed')
        self.assertFalse(self.mgr.preference_path.exists())
        self.target.write_bytes(self.original)
        self.assertTrue(self.mgr.preflight()['changed'])
        self.assertEqual(self.mgr.status()['state'], 'installed')

    def test_status_missing_installed_cli_does_not_block_removal(self):
        self.mgr.apply()
        (self.tool / 'cli.py').unlink()
        status = self.mgr.status()
        self.assertEqual(status['state'], 'dependency-missing')
        self.assertFalse(status['compatible'])
        self.assertEqual(status['bridge_dependencies']['cli']['path'], str(self.tool / 'cli.py'))
        self.assertIn(str(self.tool / 'cli.py'), status['reason'])
        self.assertTrue(self.mgr.remove()['changed'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_status_checks_old_bridge_python_and_repair_can_refresh_it(self):
        old_python = self.base / 'Old Python 中文' / 'python.exe'
        old_python.parent.mkdir()
        old_python.write_bytes(b'not executed')
        old_manager = manager.Manager(self.editor, self.tool, python_executable=old_python)
        self.assertEqual(old_manager.apply()['state'], 'installed')
        old_python.unlink()
        status = self.mgr.status()
        self.assertEqual(status['state'], 'dependency-missing')
        self.assertEqual(status['bridge_dependencies']['python']['path'], str(old_python))
        self.assertTrue(Path(sys.executable).is_file())
        self.assertEqual(self.mgr.repair()['action'], 'bridge-refreshed')
        self.assertEqual(self.mgr.status()['state'], 'installed')

    def test_status_does_not_substitute_current_python_for_live_bridge(self):
        self.mgr.apply()
        other = manager.Manager(self.editor, self.tool, python_executable=self.base / 'missing-current-python.exe')
        status = other.status()
        self.assertEqual(status['state'], 'installed')
        self.assertEqual(status['bridge_dependencies']['python']['path'], str(Path(sys.executable).resolve()))

    def test_status_checks_old_cli_after_tool_relocation(self):
        self.mgr.apply()
        relocated = self.base / 'relocated tools 中文'
        shutil.copytree(self.tool, relocated)
        newer = manager.Manager(self.editor, relocated)
        (self.tool / 'cli.py').unlink()
        self.assertTrue((relocated / 'cli.py').is_file())
        status = newer.status()
        self.assertEqual(status['state'], 'dependency-missing')
        self.assertEqual(status['bridge_dependencies']['cli']['path'], str(self.tool / 'cli.py'))
        self.assertEqual(newer.repair()['action'], 'bridge-refreshed')
        self.assertEqual(newer.status()['bridge_dependencies']['cli']['path'], str(relocated / 'cli.py'))


if __name__ == '__main__':
    unittest.main()
