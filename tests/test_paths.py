from pathlib import Path
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli
from paths import launcher_settings, resolve_editor_root


class EditorPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='KKLC paths 中文 ')
        self.base = Path(self.temporary.name)
        self.tool = self.base / 'standalone checkout'
        self.tool.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def save_settings(self, value):
        state = self.tool / 'state'
        state.mkdir(exist_ok=True)
        (state / 'launcher.json').write_text(json.dumps(value), encoding='utf-8')

    def test_explicit_environment_and_saved_settings_have_documented_priority(self):
        saved, environment, explicit = (self.base / name for name in ['saved editor', 'env editor', 'explicit editor'])
        self.save_settings({'editor_root': str(saved)})
        env = {'KKLC_EDITOR_ROOT': str(environment)}
        self.assertEqual(resolve_editor_root(explicit, tool_root=self.tool, environ=env), explicit.resolve())
        self.assertEqual(resolve_editor_root(tool_root=self.tool, environ=env), environment.resolve())
        self.assertEqual(resolve_editor_root(tool_root=self.tool, environ={}), saved.resolve())

    def test_missing_or_malformed_settings_do_not_guess_editor_or_create_state(self):
        self.assertIsNone(resolve_editor_root(tool_root=self.tool, environ={}))
        self.assertFalse((self.tool / 'state').exists())
        self.save_settings([])
        self.assertEqual(launcher_settings(self.tool), {})
        self.assertIsNone(resolve_editor_root(tool_root=self.tool, environ={}))
        (self.tool / 'state/launcher.json').write_text('{bad json', encoding='utf-8')
        self.assertIsNone(resolve_editor_root(tool_root=self.tool, environ={}))

    def test_legacy_tools_layout_is_used_only_when_compiler_exists(self):
        legacy_tool = self.base / 'tools' / 'kkwe_local_context'
        legacy_tool.mkdir(parents=True)
        candidate = self.base / 'KKWE插件'
        candidate.mkdir()
        self.assertIsNone(resolve_editor_root(tool_root=legacy_tool, environ={}))
        compiler = candidate / 'share/script/compile/wave.lua'
        compiler.parent.mkdir(parents=True)
        compiler.write_text('-- synthetic compiler', encoding='utf-8')
        self.assertEqual(resolve_editor_root(tool_root=legacy_tool, environ={}), candidate.resolve())
        # A standalone checkout does not infer the same nearby editor.
        self.assertIsNone(resolve_editor_root(tool_root=self.tool, environ={}))

    def test_empty_values_fall_through_but_configured_missing_location_is_not_replaced(self):
        missing = self.base / 'not installed'
        self.save_settings({'editor_root': str(missing)})
        self.assertEqual(resolve_editor_root('  ', tool_root=self.tool, environ={'KKLC_EDITOR_ROOT': '  '}), missing.resolve())
        self.assertFalse(missing.exists())

    def test_cli_without_configuration_explains_how_to_select_editor(self):
        output = io.StringIO()
        with patch.object(cli, 'ROOT', self.tool), patch.dict(os.environ, {}, clear=True), patch('manager.Manager') as manager:
            with patch.object(sys, 'argv', ['cli.py', 'status']), contextlib.redirect_stderr(output):
                self.assertEqual(cli.main(), 1)
            manager.assert_not_called()
        message = json.loads(output.getvalue())
        self.assertFalse(message['ok'])
        self.assertIn('--editor-root', message['error'])
        self.assertIn('KKLC_EDITOR_ROOT', message['error'])

    def test_cli_environment_location_is_passed_to_manager(self):
        selected = self.base / 'selected editor'
        with patch.object(cli, 'ROOT', self.tool), patch.dict(os.environ, {'KKLC_EDITOR_ROOT': str(selected)}, clear=True):
            with patch('manager.Manager') as manager, patch.object(sys, 'argv', ['cli.py', 'status']), contextlib.redirect_stdout(io.StringIO()):
                manager.return_value.status.return_value = {'state': 'original-supported'}
                self.assertEqual(cli.main(), 0)
                manager.assert_called_once_with(selected.resolve())


if __name__ == '__main__':
    unittest.main()
