"""Guard adapted JASS after Wave/includes and Lua templates have expanded.

This function only returns candidate text. The caller must validate the complete
result and atomically publish it; exceptions must leave the input file alone.
"""
from __future__ import annotations

import re
from typing import Any


COMMENTS_AND_QUOTES = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|/\*.*?\*/|//[^\r\n]*''', re.S)
LATER_CODE_DIRECTIVES = {
    'textmacro', 'endtextmacro', 'runtextmacro', 'import', 'inject', 'endinject',
    'external', 'externalblock', 'endexternalblock', 'loaddata',
    'novjass', 'endnovjass', 'preprocessor',
}
IDENTIFIER = r'[A-Za-z_][A-Za-z0-9_]*'


def _directives(text: str):
    """Find real bang comments, excluding strings and multiline comments."""
    for match in COMMENTS_AND_QUOTES.finditer(text):
        if match.group().startswith('//!'):
            yield match, match.group()[3:].strip()


def _macro_error(text: str, match: Any, message: str) -> ValueError:
    return ValueError(f'line {text.count(chr(10), 0, match.start()) + 1}: {message}')


def _expand_textmacros(text: str) -> tuple[str, dict]:
    """Expand the literal, non-nested JassHelper textmacro subset before guards.

    Substitution is textual, including quoted text, just like textmacro's
    $PARAMETER$ contract. Definitions are removed so JassHelper cannot generate
    additional uninspected code later. Unsupported invocation forms fail closed.
    """
    definitions = {}
    invocations = []
    edits = []
    active = None
    for match, directive in _directives(text):
        command = directive.split(None, 1)[0] if directive else ''
        if command == 'textmacro':
            if active is not None:
                raise _macro_error(text, match, 'Nested textmacro definitions are unsupported')
            header = re.fullmatch(r'textmacro\s+(' + IDENTIFIER + r')(?:\s+takes\s+(.+))?', directive)
            if header is None:
                raise _macro_error(text, match, 'Unsupported textmacro definition')
            name = header[1]
            parameters = [part.strip() for part in header[2].split(',')] if header[2] else []
            if any(re.fullmatch(IDENTIFIER, parameter) is None for parameter in parameters) or len(set(parameters)) != len(parameters):
                raise _macro_error(text, match, 'Invalid or duplicate textmacro parameters: ' + name)
            if name in definitions:
                raise _macro_error(text, match, 'Duplicate textmacro definition: ' + name)
            body_start = text.find('\n', match.end())
            # The definition directive consumes its line. A body cannot begin
            # before a newline, and EOF here necessarily leaves it unclosed.
            body_start = len(text) if body_start < 0 else body_start + 1
            active = (name, parameters, match, body_start)
        elif command == 'endtextmacro':
            if directive != 'endtextmacro' or active is None:
                raise _macro_error(text, match, 'Unmatched or malformed endtextmacro')
            name, parameters, opening, body_start = active
            body = text[body_start:match.start()]
            placeholders = set(re.findall(r'\$(' + IDENTIFIER + r')\$', body))
            if not placeholders <= set(parameters):
                raise _macro_error(text, opening, 'Unknown textmacro parameter: ' + ', '.join(sorted(placeholders - set(parameters))))
            definitions[name] = (parameters, body)
            # Preserve definition line breaks so diagnostics before the first
            # expansion retain their source line numbers.
            edits.append((opening.start(), match.end(), '\n' * text.count('\n', opening.start(), match.end())))
            active = None
        elif command == 'runtextmacro':
            if active is not None:
                raise _macro_error(text, match, 'Nested or recursive textmacro invocation is unsupported')
            invocation = re.fullmatch(r'runtextmacro\s+(' + IDENTIFIER + r')\s*\((.*)\)', directive)
            if invocation is None:
                raise _macro_error(text, match, 'Unsupported textmacro invocation')
            arguments = invocation[2].strip()
            values = []
            if arguments:
                literal_list = r'"[^"\\\r\n]*"(?:\s*,\s*"[^"\\\r\n]*")*'
                if re.fullmatch(literal_list, arguments) is None:
                    raise _macro_error(text, match, 'Textmacro arguments must be unescaped string literals')
                values = re.findall(r'"([^"\\\r\n]*)"', arguments)
            invocations.append((match, invocation[1], values))
    if active is not None:
        raise _macro_error(text, active[2], 'Unclosed textmacro definition: ' + active[0])
    for match, name, values in invocations:
        if name not in definitions:
            raise _macro_error(text, match, 'Unknown textmacro: ' + name)
        parameters, body = definitions[name]
        if len(parameters) != len(values):
            raise _macro_error(text, match, 'Unexpected textmacro argument count: ' + name)
        arguments = dict(zip(parameters, values))
        expanded = re.sub(r'\$(' + IDENTIFIER + r')\$', lambda found: arguments[found[1]], body)
        edits.append((match.start(), match.end(), expanded))
    if not edits:
        return text, {'definitions': 0, 'invocations': 0}
    pieces = []
    cursor = 0
    for begin, end, replacement in sorted(edits):
        pieces.extend((text[cursor:begin], replacement))
        cursor = end
    pieces.append(text[cursor:])
    return ''.join(pieces), {'definitions': len(definitions), 'invocations': len(invocations)}


def _error(text: str, token: Any, message: str) -> ValueError:
    return ValueError(f"line {text.count(chr(10), 0, token.start) + 1}: {message}")


def _arguments(text: str, tokens: list[Any], index: int) -> list[list[Any]]:
    """Read arguments of a global call whose next token is an opening '(' ."""
    arguments = []
    begin = index + 2
    depth = 1
    for pos in range(begin, len(tokens)):
        value = tokens[pos].value
        if value in {'(', '['}:
            depth += 1
        elif value in {')', ']'}:
            depth -= 1
            if depth == 0:
                if pos > begin or arguments:
                    arguments.append(tokens[begin:pos])
                return arguments
        elif value == ',' and depth == 1:
            arguments.append(tokens[begin:pos])
            begin = pos + 1
    raise _error(text, tokens[index], 'Unclosed final call: ' + tokens[index].value)


def _execute_func_guard(protected: set[str]) -> str:
    lines = ['function KKLC_ExecuteFuncGuarded takes string target returns nothing\n']
    groups = {}
    for name in sorted(protected):
        # Function identifiers are ASCII JASS names, never string expressions.
        if re.fullmatch(IDENTIFIER, name) is None:
            raise ValueError('Unverified ExecuteFunc guard target: ' + name)
        groups.setdefault(name[:5], []).append(name)
    for prefix, names in sorted(groups.items()):
        lines.append(f'    if SubString(target, 0, 5) == "{prefix}" then\n')
        for name in names:
            lines.extend((f'        if target == "{name}" then\n',
                          '            call KKLC_Fail("ExecuteFunc cannot supply helper context")\n',
                          '            return\n        endif\n'))
        lines.append('    endif\n')
    lines.append('    call ExecuteFunc(target)\nendfunction\n')
    return ''.join(lines)


def finalize(text: str, adapter_module: Any) -> tuple[str, dict]:
    """Finalize a marked, adapted output; reject unknown context entry paths."""
    original = text
    text, textmacros = _expand_textmacros(text)
    # JassHelper runs later. Reject deferred generation, while preserving ordinary
    # //! annotations and checking existing Zinc text below. Quoted text and block
    # comments do not activate directives.
    for match, directive in _directives(text):
        words = directive.split()
        if words and words[0] in LATER_CODE_DIRECTIVES:
            line = text.count('\n', 0, match.start()) + 1
            raise ValueError(f'line {line}: Unsupported later JassHelper directive: {match.group()}')
    # This phase still precedes JassHelper's library/scope expansion. Reuse the
    # adapter's symbol identities instead of treating every private Init as global.
    parsed = adapter_module.Adapter(text)
    parsed.parse(trusted_output=True)
    tokens = parsed.tokens
    by_name = parsed.functions
    helpers = {fn.name for fn in by_name.values()
               if parsed.compact(text[fn.takes_start:fn.takes_end]) == 'integerkklc_parent'}
    scoped_helpers = {name for name in helpers
                      if name != by_name[name].source_name or by_name[name].visibility}
    protected = helpers | {fn.name for fn in by_name.values()
                           if fn.name == fn.source_name and not fn.visibility
                           and fn.name.endswith('_KKLCBridge')}
    native_wrappers = {'DestroyTimer': ('KKLC_DestroyTimer', 'timer'),
                       'DestroyTrigger': ('KKLC_DestroyTrigger', 'trigger')}
    trusted_bodies = set()
    for native, (wrapper, type_name) in native_wrappers.items():
        fn = by_name.get(wrapper)
        if fn is None or parsed.compact(text[fn.takes_start:fn.takes_end]) != type_name + 'value' or fn.returns != 'nothing':
            raise ValueError('Missing or changed generated destruction wrapper: ' + wrapper)
        expected = ['call', 'KKLC_Unbind', '(', 'value', ')',
                    'call', native, '(', 'value', ')']
        body_tokens = parsed.tokens_between(fn.body, fn.end)
        if [t.value for t in body_tokens] != expected:
            parsed.fail('Changed generated destruction wrapper: ' + wrapper, fn.start)
        trusted_bodies.update(t.start for t in body_tokens)

    guard_name = 'KKLC_ExecuteFuncGuarded'
    guard = by_name.get(guard_name)
    if any(fn.source_name == guard_name and fn.name != guard_name for fn in by_name.values()):
        raise ValueError('Changed generated ExecuteFunc guard scope')
    if guard is not None:
        if scoped_helpers:
            raise ValueError('Scoped helpers cannot validate a generated ExecuteFunc guard')
        expected_guard = [token.value for token in adapter_module.lex(_execute_func_guard(protected))]
        actual_guard = [token.value for token in parsed.tokens_between(guard.start, guard.finish)]
        if guard.visibility or actual_guard != expected_guard:
            parsed.fail('Changed generated ExecuteFunc guard or protected targets', guard.start)
        trusted_bodies.update(t.start for t in parsed.tokens_between(guard.body, guard.end))

    replacements = []
    counts = {name: 0 for name in native_wrappers}
    guarded_calls = 0
    zinc = False
    # Zinc uses bare callback names as well as `function Name`. Its full symbol
    # semantics are not migrated: reject crossings into context helpers and native
    # shadowing, but still wrap the visible native destruction calls in its text.
    helper_spellings = helpers | {by_name[name].source_name for name in helpers}
    helper_spellings.update(alias for alias, name in parsed.public_functions.items() if name in helpers)
    for index, token in enumerate(tokens):
        if token.kind == 'directive':
            if token.value == 'zinc':
                if zinc:
                    raise _error(text, token, 'Nested Zinc block')
                zinc = True
            elif token.value == 'endzinc':
                if not zinc:
                    raise _error(text, token, 'Unmatched endzinc')
                zinc = False
            continue
        if token.kind != 'id':
            continue
        if zinc and token.value in helper_spellings:
            raise _error(text, token, 'Zinc reference cannot supply helper context: ' + token.value)
        if zinc and token.value == 'function' and index + 1 < len(tokens):
            target = tokens[index + 1].value
            if target in {*native_wrappers, 'ExecuteFunc'}:
                raise _error(text, token, 'Unsupported Zinc native shadowing: ' + target)
        if token.value == 'function' and index + 1 < len(tokens):
            target_token = tokens[index + 1]
            target = parsed.resolve(target_token.value, target_token.start)
            declaration = index + 2 < len(tokens) and tokens[index + 2].value == 'takes'
            if target in helpers and not declaration:
                raise _error(text, token, 'Final code reference cannot supply helper context: ' + target)
        if index + 1 >= len(tokens) or tokens[index + 1].value != '(':
            continue
        if index > 0 and tokens[index - 1].value == '.':
            continue  # A vJASS member method is not the native global function.
        if index > 0 and tokens[index - 1].value == 'method':
            continue  # Zinc method declarations also precede '(' directly.
        resolved = parsed.resolve(token.value, token.start)
        if resolved in native_wrappers and token.start not in trusted_bodies:
            arguments = _arguments(text, tokens, index)
            if len(arguments) != 1 or not arguments[0]:
                raise _error(text, token, 'Unexpected final destruction arguments: ' + token.value)
            replacements.append((token.start, token.end, native_wrappers[resolved][0]))
            counts[resolved] += 1
        elif resolved == 'ExecuteFunc' and helpers and token.start not in trusted_bodies:
            arguments = _arguments(text, tokens, index)
            if len(arguments) != 1 or not arguments[0]:
                raise _error(text, token, 'Unexpected ExecuteFunc arguments')
            if any(part.kind == 'string' and '\\' in part.value for part in arguments[0]):
                raise _error(text, token, 'Escaped ExecuteFunc target is unverified while context helpers exist')
            if len(arguments[0]) == 1 and arguments[0][0].kind == 'string':
                target = arguments[0][0].value[1:-1]
                if parsed.resolve(target, token.start) in helpers or target in protected:
                    raise _error(text, token, 'ExecuteFunc cannot supply helper context: ' + target)
            else:
                if scoped_helpers:
                    raise _error(text, token, 'Dynamic ExecuteFunc target is unverified while scoped context helpers exist')
                replacements.append((token.start, token.end, guard_name))
                guarded_calls += 1

    if zinc:
        raise ValueError('Unclosed Zinc block')

    if guarded_calls and guard is None:
        insertion = by_name['KKLC_DestroyTrigger'].finish
        replacements.append((insertion, insertion, '\n' + _execute_func_guard(protected)))

    updated = text
    for start, end, replacement in sorted(replacements, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    # Reuse the adapter's strict owner/condition/action and wait checks on the
    # expanded output, where previously hidden registrations are now visible.
    callbacks = adapter_module.validate_generated_callbacks(updated)
    return updated, {'changed': updated != original, 'destroy_calls_wrapped': counts,
                     'context_helpers': sorted(helpers), 'callbacks': callbacks,
                     'textmacros': textmacros, 'execute_func_guarded_calls': guarded_calls,
                     'execute_func_protected_count': len(protected),
                     'execute_func_guard_present': guard is not None or guarded_calls > 0}
