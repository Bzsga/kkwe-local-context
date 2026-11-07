"""Install a removable compiler bridge without restoring old editor files.

The compatibility profile fingerprints Lua tokens, not editor version strings.
Unknown executable Lua changes are rejected. Comments, whitespace, and a newer
editor executable do not invalidate an otherwise identical compiler pipeline.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
TARGET = Path('share/script/compile/wave.lua')
BEGIN = '-- KKWE_LOCAL_CONTEXT_BRIDGE_BEGIN v1'
END = '-- KKWE_LOCAL_CONTEXT_BRIDGE_END v1'
ANCHOR = re.compile(r'(?m)^function wave:compile\(op\)[ \t]*(\r?\n)')
BLOCK = re.compile(r'(?m)^\t-- KKWE_LOCAL_CONTEXT_BRIDGE_BEGIN v1\r?\n.*?^\t-- KKWE_LOCAL_CONTEXT_BRIDGE_END v1\r?\n', re.S)
TOKEN = re.compile(r'''--\[(=*)\[.*?\]\1\]|--[^\r\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\[(=*)\[.*?\]\2\]|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|\.\.\.|\.\.|==|~=|<=|>=|//|<<|>>|::|[^\s]''', re.S)
BRIDGE_DEPENDENCIES = re.compile(r'''local\s+kklc_command\s*=\s*kklc_quote\(\s*("(?:\\.|[^"\\])*")\s*\)\s*\.\.\s*' -X utf8 -B '\s*\.\.\s*kklc_quote\(\s*("(?:\\.|[^"\\])*")\s*\)''')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def semantic_digest(text: str) -> str:
    """Ignore spacing between tokens; retain strings and complete operators."""
    tokens = [match.group() for match in TOKEN.finditer(text.lstrip('\ufeff'))
              if not match.group().startswith('--')]
    return digest(json.dumps(tokens, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def compiler_pipeline(editor_root: str | Path) -> dict[str, Any]:
    """Read the active compiler choices without changing user configuration."""
    config_path = Path(editor_root) / 'bin/EverConfig.cfg'
    options = {'EnableCJass': '0', 'EnableJassHelper': '1'}
    try:
        if config_path.is_file():
            raw = config_path.read_bytes()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gb18030')
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read_string(text)
            for key, default in options.items():
                options[key] = config.get('ScriptCompiler', key, fallback=default).strip()
    except (OSError, UnicodeError, configparser.Error) as error:
        return {'compatible': False, 'source': str(config_path),
                'reason': 'Cannot verify compiler settings: ' + str(error)}
    supported = options['EnableCJass'] == '0' and options['EnableJassHelper'] == '1'
    return {'compatible': supported, 'source': str(config_path), 'options': options,
            'reason': 'Ordinary JASS/Wave pipeline' if supported else
                      'Only EnableCJass=0 and EnableJassHelper=1 are supported; user settings were preserved'}


def atomic_write(path: Path, data: bytes, expected: bytes | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        if expected is not None and (not path.is_file() or path.read_bytes() != expected):
            raise RuntimeError('Target changed during operation; no replacement was made')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Manager:
    def __init__(self, editor_root: str | Path, tool_root: str | Path = TOOL_ROOT,
                 python_executable: str | Path = sys.executable):
        self.editor_root = Path(editor_root).resolve()
        self.tool_root = Path(tool_root).resolve()
        self.python = Path(python_executable).resolve()
        self.target = self.editor_root / TARGET
        self.state_root = self.tool_root / 'state'
        self.identity = digest(os.path.normcase(str(self.editor_root)).encode('utf-8'))[:20]
        self.manifest_path = self.state_root / (self.identity + '.json')
        self.preference_path = self.state_root / 'preferences' / (self.identity + '.json')

    def _read_preference(self, raw: bytes | None = None) -> bool:
        if raw is None:
            if not self.preference_path.exists():
                return True  # Preserve existing launcher behavior until explicitly disabled.
            raw = self.preference_path.read_bytes()
        record = json.loads(raw.decode('utf-8'))
        if record.get('format') != 1 or record.get('editor_root') != str(self.editor_root):
            raise ValueError('Automatic repair preference belongs to another editor or format')
        if type(record.get('auto_repair')) is not bool:
            raise ValueError('Automatic repair preference must contain a boolean auto_repair')
        return record['auto_repair']

    def _set_auto_repair(self, enabled: bool) -> bool:
        previous = self.preference_path.read_bytes() if self.preference_path.exists() else None
        if previous is not None and self._read_preference(previous) == enabled:
            return False
        record = {'format': 1, 'editor_root': str(self.editor_root), 'auto_repair': enabled}
        atomic_write(self.preference_path,
                     (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
                     expected=previous)
        return True

    def _with_preference(self, result: dict[str, Any]) -> dict[str, Any]:
        result = dict(result)
        try:
            result['auto_repair'] = self._read_preference()
            result['preference_path'] = str(self.preference_path)
        except (OSError, UnicodeError, ValueError) as error:
            result.update(compiler_state=result['state'], state='preference-error',
                          auto_repair=None, reason='Cannot verify automatic repair preference: ' + str(error))
        return result

    def _profile(self) -> dict[str, Any]:
        return json.loads((self.tool_root / 'bootstrap/compatibility.json').read_text(encoding='utf-8'))

    def _manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        result = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        if result.get('editor_root') != str(self.editor_root):
            raise ValueError('Installation record belongs to a different editor')
        return result

    def bridge(self, newline: str = '\n') -> str:
        template = (self.tool_root / 'bootstrap/bridge.lua.in').read_text(encoding='utf-8')
        for key, value in {'PYTHON': self.python, 'CLI': self.tool_root / 'cli.py', 'EDITOR': self.editor_root}.items():
            if any(c in str(value) for c in ('"', '\n', '\r', '\0')):
                raise ValueError('Unsupported path character in ' + key)
            template = template.replace('@@' + key + '@@', json.dumps(str(value), ensure_ascii=False))
        return template.replace('\n', newline)

    def _inspect(self) -> tuple[dict[str, Any], bytes | None, str | None, re.Match[str] | None]:
        result: dict[str, Any] = {'state': 'upstream-changed', 'changed': False, 'compatible': False,
                                  'editor_root': str(self.editor_root), 'target': str(self.target)}
        if not self.target.is_file():
            result['reason'] = 'Compiler source is missing'
            return result, None, None, None
        raw = self.target.read_bytes()
        result['sha256'] = digest(raw)
        try:
            text = raw.decode('utf-8')
            profile = self._profile()
            manifest = self._manifest()
        except (UnicodeError, OSError, ValueError) as error:
            result['reason'] = 'Cannot verify source or installation metadata: ' + str(error)
            return result, raw, None, None
        blocks = list(BLOCK.finditer(text))
        if text.count(BEGIN) != len(blocks) or text.count(END) != len(blocks) or len(blocks) > 1:
            result.update(state='conflict', reason='Incomplete or duplicate managed bridge markers')
            return result, raw, text, None
        block = blocks[0] if blocks else None
        if block:
            newline = '\r\n' if '\r\n' in block.group() else '\n'
            known = [self.bridge(newline)]
            if manifest:
                for field in ('bridge', 'previous_bridge'):
                    if isinstance(manifest.get(field), str):
                        known.append(manifest[field])
            if block.group() not in known:
                result.update(state='conflict', reason='Managed bridge has been edited; refusing to overwrite it')
                return result, raw, text, block
        clean = text[:block.start()] + text[block.end():] if block else text
        result['bridge_present'] = bool(block)
        anchors = list(ANCHOR.finditer(clean))
        supported = next((entry for entry in profile['profiles']
                          if semantic_digest(clean) == entry['semantic_sha256']), None)
        if len(anchors) != 1 or not supported:
            result['reason'] = 'Lua compiler content does not match a reviewed compatibility profile'
            return result, raw, text, block
        try:
            generator_profile = json.loads((self.tool_root / 'compatibility.json').read_text(encoding='utf-8'))
            relative_files = generator_profile['relative_files']
            if not isinstance(relative_files, dict) or 'plugin/YDTrigger.dll' not in relative_files:
                raise ValueError('Generator compatibility profile must pin plugin/YDTrigger.dll')
            checked_files = {}
            for relative, expected in relative_files.items():
                relative_path = Path(relative)
                if relative_path.is_absolute() or '..' in relative_path.parts:
                    raise ValueError('Compatibility path must stay inside the editor')
                expected_hash = expected['sha256'] if isinstance(expected, dict) else expected
                file_path = self.editor_root / relative_path
                actual_hash = digest(file_path.read_bytes()) if file_path.is_file() else None
                checked_files[relative] = {'expected': expected_hash, 'actual': actual_hash}
                if actual_hash != expected_hash:
                    result.update(reason='Generator contract changed or is missing: ' + relative,
                                  checked_files=checked_files)
                    return result, raw, text, block
        except (OSError, ValueError, KeyError, TypeError) as error:
            result['reason'] = 'Cannot verify generator compatibility: ' + str(error)
            return result, raw, text, block
        pipeline = compiler_pipeline(self.editor_root)
        result['pipeline'] = pipeline
        if not pipeline['compatible']:
            result.update(state='unsupported-pipeline', reason=pipeline['reason'])
            return result, raw, text, block
        result['support'] = {'profile': supported['id'],
                             'verification': 'exact-bytes' if digest(clean.encode('utf-8')) == supported['sha256'] else 'same-lua-tokens',
                             'editor_version_is_not_a_gate': True}
        result.update(state='installed' if block else 'original-supported', compatible=True,
                      checked_files=checked_files, reason='Verified compiler content and generator contract')
        if block:
            expected_pos = ANCHOR.search(text).end() if ANCHOR.search(text) else -1
            if block.start() != expected_pos:
                result.update(state='conflict', compatible=False, reason='Managed bridge moved away from the reviewed insertion point')
        return result, raw, text, block

    def status(self) -> dict[str, Any]:
        result, _, _, block = self._inspect()
        # _inspect establishes bridge ownership separately, so a missing
        # interpreter/CLI never prevents removing or refreshing a known block.
        if result['state'] == 'installed' and block is not None:
            try:
                matches = list(BRIDGE_DEPENDENCIES.finditer(block.group()))
                if len(matches) != 1:
                    raise ValueError('Cannot identify the installed bridge command dependencies')
                checked = {}
                for label, literal in zip(('python', 'cli'), matches[0].groups()):
                    reference = json.loads(literal)
                    path = Path(reference)
                    if not path.is_absolute():
                        raise ValueError('Installed bridge dependency is not an absolute path: ' + reference)
                    checked[label] = {'path': reference, 'exists': path.is_file()}
                result['bridge_dependencies'] = checked
                missing = [label + ': ' + item['path'] for label, item in checked.items() if not item['exists']]
                if missing:
                    result.update(state='dependency-missing', compatible=False,
                                  reason='Installed bridge dependency is missing: ' + '; '.join(missing))
            except (OSError, ValueError, TypeError) as error:
                result.update(state='dependency-missing', compatible=False,
                              reason='Cannot verify installed bridge dependencies: ' + str(error))
        return self._with_preference(result)

    def _record(self, baseline: bytes, bridge: str, previous_bridge: str | None = None) -> None:
        baseline_hash = digest(baseline)
        backup = self.state_root / 'backups' / (baseline_hash + '.wave.lua')
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if backup.read_bytes() != baseline:
                raise RuntimeError('Baseline backup content failed verification')
        else:
            with backup.open('xb') as stream:
                stream.write(baseline)
        record = {'format': 1, 'editor_root': str(self.editor_root), 'target': str(self.target),
                  'python': str(self.python), 'baseline_sha256': baseline_hash,
                  'backup': str(backup), 'bridge': bridge}
        if previous_bridge is not None:
            # The target is replaced after this record. Keep the exact block
            # just verified by _inspect recoverable if a later write fails.
            record['previous_bridge'] = previous_bridge
        atomic_write(self.manifest_path, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        atomic_write(self.state_root / 'launcher.json', (json.dumps({'python': str(self.python), 'editor_root': str(self.editor_root)}, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

    def apply(self) -> dict[str, Any]:
        """Explicit installation also re-enables future launcher recovery."""
        return self._apply(explicit=True)

    def _apply(self, *, explicit: bool) -> dict[str, Any]:
        result, raw, text, block = self._inspect()
        if result['state'] not in ('installed', 'original-supported'):
            return self._with_preference(result)
        if not self.python.is_file() or not (self.tool_root / 'cli.py').is_file():
            return self._with_preference(dict(result, state='dependency-missing', reason='Python executable or transformer CLI is missing'))
        preference_changed = self._set_auto_repair(True) if explicit else False
        if not explicit and not self._read_preference():
            return self._disabled_preflight()
        assert raw is not None and text is not None
        if block is not None:
            newline = '\r\n' if '\r\n' in block.group() else '\n'
            bridge = self.bridge(newline)
            if block.group() == bridge:
                return dict(self.status(), preference_changed=preference_changed)
            updated = text[:block.start()] + bridge + text[block.end():]
            clean = text[:block.start()] + text[block.end():]
            self._record(clean.encode('utf-8'), bridge, previous_bridge=block.group())
            if not explicit and not self._read_preference():
                return self._disabled_preflight()
            atomic_write(self.target, updated.encode('utf-8'), expected=raw)
            return dict(self.status(), changed=True, preference_changed=preference_changed, action='bridge-refreshed')
        anchor = ANCHOR.search(text)
        assert anchor is not None
        bridge = self.bridge(anchor.group(1))
        updated = text[:anchor.end()] + bridge + text[anchor.end():]
        self._record(raw, bridge)
        if not explicit and not self._read_preference():
            return self._disabled_preflight()
        atomic_write(self.target, updated.encode('utf-8'), expected=raw)
        return dict(self.status(), changed=True, preference_changed=preference_changed, action='applied')

    def repair(self) -> dict[str, Any]:
        """Reapply only on reviewed content after an updater removes the bridge."""
        return self.apply()

    def _disabled_preflight(self) -> dict[str, Any]:
        inspected, raw, _, block = self._inspect()
        result = self._with_preference(inspected)
        if result['state'] == 'preference-error':
            return result
        if block is not None or (raw is not None and b'KKWE_LOCAL_CONTEXT_BRIDGE_' in raw):
            return dict(result, compiler_state=result['state'], state='removal-incomplete', changed=False,
                        bridge_present=True, action='automatic-repair-disabled',
                        reason='Automatic recovery is disabled, but the managed bridge remains; complete removal in the manager before launch')
        return dict(result, compiler_state=result['state'], state='disabled', changed=False,
                    action='automatic-repair-disabled',
                    reason='Automatic bridge recovery is disabled by user choice')

    def preflight(self) -> dict[str, Any]:
        """Launcher check: an explicit uninstall must never reinstall itself."""
        if not self._read_preference():
            return self._disabled_preflight()
        return self._apply(explicit=False)

    def remove(self) -> dict[str, Any]:
        # Persist the opt-out before touching the bridge. If persistence fails,
        # retain the bridge; a successful removal must not be undone next launch.
        preference_changed = self._set_auto_repair(False)
        result, raw, text, block = self._inspect()
        if result['state'] == 'conflict':
            return dict(self._with_preference(result), preference_changed=preference_changed)
        if block is None:
            if raw is not None and b'KKWE_LOCAL_CONTEXT_BRIDGE_' in raw:
                return dict(self._with_preference(result), state='conflict', bridge_present=True,
                            preference_changed=preference_changed,
                            reason='Cannot verify the remaining managed bridge for removal; automatic recovery is disabled')
            return dict(self._with_preference(result), preference_changed=preference_changed,
                        reason='No managed bridge is present; automatic recovery is disabled')
        assert raw is not None and text is not None
        # Even if upstream code changed, remove only our verified exact block.
        updated = text[:block.start()] + text[block.end():]
        atomic_write(self.target, updated.encode('utf-8'), expected=raw)
        return dict(self.status(), changed=True, preference_changed=preference_changed, action='removed')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'apply', 'remove', 'repair', 'preflight'])
    parser.add_argument('--editor-root', required=True, type=Path)
    parser.add_argument('--tool-root', type=Path, default=TOOL_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        result = getattr(Manager(args.editor_root, args.tool_root), args.command)()
    except (OSError, ValueError, RuntimeError) as error:
        result = {'state': 'error', 'changed': False, 'reason': str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == 'remove':
        return 0 if result['state'] not in ('error', 'conflict', 'preference-error') else 2
    if args.command == 'preflight' and result['state'] == 'disabled':
        return 0
    return 0 if result['state'] in ('installed', 'original-supported') else 2


if __name__ == '__main__':
    raise SystemExit(main())
