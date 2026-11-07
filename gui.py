"""Chinese desktop manager for the removable KKWE compiler bridge."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk
from typing import Callable

from manager import Manager, TOOL_ROOT
from paths import resolve_editor_root


def console_python(executable: str | Path | None = None) -> Path:
    """Keep the compiler bridge on console Python when the UI uses pythonw."""
    path = Path(executable or sys.executable).resolve()
    sibling = path.with_name('python.exe')
    if path.name.lower() == 'pythonw.exe' and sibling.is_file():
        return sibling
    return path


def default_editor(explicit: str | Path | None = None) -> str:
    selected = resolve_editor_root(explicit, tool_root=TOOL_ROOT)
    return str(selected) if selected is not None else ''


class TaskRunner:
    """One background operation; no Tk calls or product launches in the worker."""
    def __init__(self, manager_factory: Callable | None = None):
        self.manager_factory = manager_factory or (
            lambda path: Manager(path, python_executable=console_python()))
        self.results: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._busy = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def submit(self, operation: str, editor: str) -> bool:
        if operation not in {'status', 'apply', 'remove'}:
            raise ValueError('Unknown management operation: ' + operation)
        with self._lock:
            if self._busy:
                return False
            self._busy = True

        def run():
            try:
                if not editor.strip():
                    raise ValueError('请先选择 KKWE 编辑器目录。')
                result = getattr(self.manager_factory(editor.strip()), operation)()
            except Exception as error:
                result = {'state': 'error', 'changed': False, 'reason': str(error)}
            finally:
                with self._lock:
                    self._busy = False
            self.results.put({'operation': operation, 'editor': editor, 'result': result})

        threading.Thread(target=run, name='KKLC-manager', daemon=True).start()
        return True


def present_result(operation: str, result: dict) -> tuple[str, bool]:
    """Return a concise Chinese status plus whether details need attention."""
    state = result.get('state', 'error')
    if operation == 'remove' and result.get('action') == 'removed':
        return '已卸载，自动恢复已关闭。请重启已打开的 KKWE 后再保存；已生成的地图保持原样。', False
    if state == 'installed':
        if result.get('auto_repair') is False:
            return '接入仍存在，自动恢复已关闭。可再次卸载或查看详情。', True
        return ('已安装。请重启已打开的 KKWE，再保存地图使用当前修复。' if operation == 'apply'
                else '已安装，当前编译链兼容。'), False
    if state == 'original-supported':
        if result.get('auto_repair') is False:
            return '未安装，启动自动恢复已关闭。', False
        return '尚未安装。可点击“安装 / 恢复”。', False
    labels = {
        'conflict': '接入文件存在冲突，未覆盖原内容。',
        'upstream-changed': '当前编辑器内容尚未通过兼容检查。',
        'unsupported-pipeline': '当前编译设置不受支持，请查看详情。',
        'dependency-missing': 'Python 或转换器文件缺失，请查看详情。',
        'preference-error': '无法读取自动恢复设置，请查看详情。',
        'removal-incomplete': '卸载尚未完成，接入仍存在。请先处理详情中的错误。',
    }
    if state in labels:
        message = labels[state]
        if operation == 'remove' and result.get('auto_repair') is False:
            message += ' 自动恢复已关闭。'
        return message, True
    return '操作失败：' + str(result.get('reason', '未返回可识别的结果。')), True


class ManagementWindow:
    def __init__(self, root: tk.Tk, editor_root: str | None = None):
        self.root = root
        self.runner = TaskRunner()
        self.closing = False
        self.root.title('KKWE 防串值修复管理')
        self.root.geometry('760x340')
        self.root.minsize(640, 340)
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 17, 'bold'))
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 11, 'bold'))
        style.configure('Hint.TLabel', foreground='#5b6470')
        main = ttk.Frame(root, padding=(24, 18))
        main.pack(fill='both', expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(6, weight=1)
        ttk.Label(main, text='防串值修复', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(main, text='安装后重新保存地图生效；更新 KKWE 后可在这里恢复接入。',
                  style='Hint.TLabel').grid(row=1, column=0, sticky='w', pady=(5, 18))
        path_row = ttk.Frame(main)
        path_row.grid(row=2, column=0, sticky='ew')
        path_row.columnconfigure(1, weight=1)
        ttk.Label(path_row, text='编辑器目录').grid(row=0, column=0, padx=(0, 10))
        self.path = tk.StringVar(value=default_editor(editor_root))
        self.path_entry = ttk.Entry(path_row, textvariable=self.path, state='readonly')
        self.path_entry.grid(row=0, column=1, sticky='ew')
        self.browse = ttk.Button(path_row, text='选择…', command=self.choose_folder, width=8)
        self.browse.grid(row=0, column=2, padx=(10, 0))
        status_panel = ttk.Frame(main)
        status_panel.grid(row=3, column=0, sticky='ew', pady=(20, 12))
        self.status = tk.StringVar(value='正在读取状态…')
        self.status_label = ttk.Label(status_panel, textvariable=self.status, style='Status.TLabel', wraplength=680)
        self.status_label.pack(anchor='w')
        status_panel.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(200, event.width)))
        self.automatic = tk.StringVar(value='启动入口自动恢复：检查中')
        ttk.Label(status_panel, textvariable=self.automatic,
                  style='Hint.TLabel').pack(anchor='w', pady=(6, 0))
        actions = ttk.Frame(main)
        actions.grid(row=4, column=0, sticky='ew')
        self.buttons = []
        for col, (label, operation) in enumerate((('安装 / 恢复', 'apply'), ('卸载', 'remove'), ('刷新', 'status'))):
            button = ttk.Button(actions, text=label, command=lambda op=operation: self.perform(op), width=15)
            button.grid(row=0, column=col, padx=(0, 10))
            self.buttons.append(button)
        self.progress = ttk.Progressbar(actions, mode='indeterminate', length=105)
        self.progress.grid(row=0, column=3, sticky='e')
        actions.columnconfigure(3, weight=1)
        self.show_details = tk.BooleanVar(value=False)
        ttk.Checkbutton(main, text='显示详细结果', variable=self.show_details,
                        command=self.toggle_details).grid(row=5, column=0, sticky='w', pady=(16, 0))
        self.details = scrolledtext.ScrolledText(main, height=14, wrap='word', state='disabled',
                                               font=('Consolas', 9), relief='solid', borderwidth=1)
        self.details.grid(row=6, column=0, sticky='nsew', pady=(8, 0))
        self.details.grid_remove()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(80, self.poll)
        if self.path.get():
            self.perform('status')
        else:
            self.status.set('请选择 KKWE 编辑器目录。')
            self.automatic.set('启动入口自动恢复：未选择编辑器')

    def set_busy(self, busy: bool):
        state = 'disabled' if busy else 'normal'
        self.path_entry.configure(state='disabled' if busy else 'readonly')
        for widget in [self.browse, *self.buttons]:
            widget.configure(state=state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def choose_folder(self):
        selected = filedialog.askdirectory(parent=self.root, title='选择包含 KKWE.exe 的编辑器目录',
                                           initialdir=self.path.get() or str(TOOL_ROOT))
        if selected:
            self.path.set(selected)
            self.perform('status')

    def perform(self, operation: str):
        if self.runner.submit(operation, self.path.get()):
            self.set_busy(True)
            self.status.set({'status': '正在读取状态…', 'apply': '正在安装或恢复接入…',
                             'remove': '正在卸载并关闭自动恢复…'}[operation])

    def toggle_details(self):
        width = max(self.root.winfo_width(), self.root.minsize()[0])
        if self.show_details.get():
            self.details.grid()
            self.root.geometry(f'{width}x620')
        else:
            self.details.grid_remove()
            self.root.geometry(f'{width}x340')

    def poll(self):
        try:
            item = self.runner.results.get_nowait()
        except queue.Empty:
            item = None
        if item is not None:
            self.set_busy(False)
            result = item['result']
            message, needs_attention = present_result(item['operation'], result)
            self.status.set(message)
            setting = result.get('auto_repair')
            self.automatic.set('启动入口自动恢复：' + ('已启用' if setting is True else '已关闭' if setting is False else '未知'))
            self.details.configure(state='normal')
            self.details.delete('1.0', 'end')
            self.details.insert('1.0', json.dumps(result, ensure_ascii=False, indent=2))
            self.details.configure(state='disabled')
            if needs_attention:
                self.show_details.set(True)
                self.toggle_details()
            if self.closing:
                self.root.destroy()
                return
        self.root.after(80, self.poll)

    def close(self):
        if self.runner.busy:
            self.closing = True
            self.set_busy(True)
            self.status.set('正在完成当前操作，结束后关闭。')
        else:
            self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor-root')
    args = parser.parse_args()
    root = tk.Tk()
    ManagementWindow(root, args.editor_root)
    root.mainloop()


if __name__ == '__main__':
    main()
