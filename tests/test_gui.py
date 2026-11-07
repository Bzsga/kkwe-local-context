from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

try:
    import tkinter
except ImportError as error:
    raise unittest.SkipTest('Tkinter is optional and unavailable on this Python installation') from error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui
from gui import ManagementWindow, TaskRunner, console_python, default_editor, present_result


class GuiControllerTests(unittest.TestCase):
    def test_standalone_checkout_starts_without_a_guessed_editor(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            with patch.object(gui, 'TOOL_ROOT', Path(temporary)):
                self.assertEqual(default_editor(), '')

    def test_explicit_editor_has_priority_over_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            explicit = Path(temporary) / 'chosen editor'
            with patch.dict(os.environ, {'KKLC_EDITOR_ROOT': str(Path(temporary) / 'environment editor')}):
                self.assertEqual(default_editor(explicit), str(explicit.resolve()))

    def test_runs_actions_off_the_caller_thread_and_reports_result(self):
        caller = threading.get_ident()
        seen = []
        class FakeManager:
            def status(self):
                seen.append(threading.get_ident())
                return {'state': 'installed', 'auto_repair': True}
        runner = TaskRunner(lambda path: FakeManager())
        self.assertTrue(runner.submit('status', 'C:\\Editor 中文 with spaces'))
        result = runner.results.get(timeout=2)
        self.assertEqual(result['result']['state'], 'installed')
        self.assertNotEqual(seen, [caller])
        self.assertFalse(runner.busy)

    def test_busy_runner_rejects_duplicate_operation(self):
        release = threading.Event()
        entered = threading.Event()
        class FakeManager:
            def apply(self):
                entered.set()
                release.wait(2)
                return {'state': 'installed'}
        runner = TaskRunner(lambda path: FakeManager())
        self.assertTrue(runner.submit('apply', 'Editor'))
        self.assertTrue(entered.wait(1))
        self.assertFalse(runner.submit('remove', 'Editor'))
        release.set()
        self.assertEqual(runner.results.get(timeout=2)['operation'], 'apply')

    def test_worker_failure_is_visible_and_does_not_stick_busy(self):
        class FakeManager:
            def remove(self):
                raise PermissionError('目标文件被占用')
        runner = TaskRunner(lambda path: FakeManager())
        runner.submit('remove', 'Editor')
        result = runner.results.get(timeout=2)['result']
        self.assertEqual(result['state'], 'error')
        self.assertIn('目标文件被占用', result['reason'])
        self.assertFalse(runner.busy)
        self.assertTrue(present_result('remove', result)[1])

    def test_no_launch_operation_and_empty_path_does_not_create_manager(self):
        runner = TaskRunner(lambda path: self.fail('must not construct a manager'))
        with self.assertRaises(ValueError):
            runner.submit('launch', 'Editor')
        runner.submit('apply', '  ')
        result = runner.results.get(timeout=2)['result']
        self.assertEqual(result['state'], 'error')
        self.assertIn('选择', result['reason'])

    def test_pythonw_ui_keeps_bridge_on_console_python(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / 'python.exe').write_bytes(b'fixture')
            (folder / 'pythonw.exe').write_bytes(b'fixture')
            self.assertEqual(console_python(folder / 'pythonw.exe'), (folder / 'python.exe').resolve())

    def test_uninstall_success_and_remaining_bridge_have_distinct_messages(self):
        message, error = present_result('remove', {'state': 'upstream-changed', 'action': 'removed', 'auto_repair': False})
        self.assertIn('已卸载', message)
        self.assertFalse(error)
        message, error = present_result('status', {'state': 'installed', 'auto_repair': False})
        self.assertIn('接入仍存在', message)
        self.assertTrue(error)

    def test_busy_state_restores_readonly_path_and_enabled_selection(self):
        class Widget:
            def configure(self, **kwargs):
                self.state = kwargs['state']
            def start(self, interval):
                pass
            def stop(self):
                pass
        window = ManagementWindow.__new__(ManagementWindow)
        window.path_entry = Widget()
        window.browse = Widget()
        window.buttons = [Widget(), Widget(), Widget()]
        window.progress = Widget()
        window.set_busy(True)
        self.assertEqual(window.path_entry.state, 'disabled')
        window.set_busy(False)
        self.assertEqual(window.path_entry.state, 'readonly')
        self.assertEqual(window.browse.state, 'normal')
        self.assertTrue(all(button.state == 'normal' for button in window.buttons))

    def test_detail_toggle_preserves_user_window_width(self):
        class Root:
            geometries = []
            def winfo_width(self):
                return 915
            def minsize(self):
                return (640, 340)
            def geometry(self, value):
                self.geometries.append(value)
        class Details:
            def grid(self):
                pass
            def grid_remove(self):
                pass
        class Flag:
            value = True
            def get(self):
                return self.value
        window = ManagementWindow.__new__(ManagementWindow)
        window.root, window.details, window.show_details = Root(), Details(), Flag()
        window.toggle_details()
        window.show_details.value = False
        window.toggle_details()
        self.assertEqual(window.root.geometries, ['915x620', '915x340'])


if __name__ == '__main__':
    unittest.main()
