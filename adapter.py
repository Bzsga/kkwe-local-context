"""Strict, pre-Wave adapter for the observed KKWE YDLocal macro dialect.

transform(source) returns (source, JSON-serializable metadata). It never does I/O.
The caller prepends the matching runtime and YDTrigger macro include. Unsupported
constructs raise TransformError before any output is returned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from bisect import bisect_left, bisect_right
import re


class TransformError(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    kind: str = "token"


def lex(text: str, include_directives=False) -> list[Token]:
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            if include_directives:
                directive = re.fullmatch(r"//!\s*(zinc|endzinc)\s*", text[i:len(text) if j < 0 else j].strip())
                if directive:
                    result.append(Token(directive[1], i, len(text) if j < 0 else j, "directive"))
            i = len(text) if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise TransformError("Unterminated block comment")
            i = j + 2
        elif c in '\"\'':
            start, quote = i, c
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                elif text[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
            else:
                raise TransformError("Unterminated string/rawcode")
            result.append(Token(text[start:i], start, i, "string" if quote == '\"' else "rawcode"))
        elif c.isalpha() or c == "_":
            start = i
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] == "_"):
                i += 1
            result.append(Token(text[start:i], start, i, "id"))
        else:
            result.append(Token(c, i, i + 1))
            i += 1
    return result


def token_lines(text: str):
    """Whole-text lexing keeps multiline comments intact during line routing."""
    tokens = lex(text)
    start = index = 0
    for line in text.splitlines(keepends=True):
        end = start + len(line)
        first = index
        while index < len(tokens) and tokens[index].start < end:
            index += 1
        yield line, tokens[first:index]
        start = end


@dataclass
class Call:
    name: str
    start: int
    end: int
    args: list[tuple[int, int]]
    source_name: str = ""


@dataclass
class Function:
    name: str
    start: int
    body: int
    end: int
    finish: int
    takes_start: int
    takes_end: int
    returns: str
    source_name: str = ""
    scope: tuple[str, ...] = ()
    visibility: str = ""
    calls: list[Call] = field(default_factory=list)
    call_starts: list[int] = field(default_factory=list)
    role: str = ""
    symbols: set[tuple[str, str, bool]] = field(default_factory=set)
    reads: set[tuple[str, str, bool]] = field(default_factory=set)
    writes: set[tuple[str, str, bool]] = field(default_factory=set)
    owner_role: str = ""
    frame_symbols: set[tuple[str, str, bool]] = field(default_factory=set)


TYPE_BASE = {x: "integer" for x in "integer unitcode abilcode itemcode frame doodad techcode buffcode destructablecode gamekey xlsxworksheet".split()}
TYPE_BASE.update({x: "real" for x in "real degree radian".split()})
TYPE_BASE.update({x: "string" for x in "string StringExt imagefile modelfile".split()})
TYPE_BASE["boolean"] = "boolean"
HANDLES = set("timer trigger unit item group player location destructable force rect region sound effect unitpool itempool quest questitem timerdialog leaderboard multiboard multiboarditem trackable dialog button texttag lightning image fogstate fogmodifier ubersplat dzeffectgroup".split())
TYPE_BASE.update({x: x for x in HANDLES})
SYNC_CALLBACKS = {"ForGroup", "ForForce", "ForGroupBJ", "ForForceBJ", "GroupEnumUnitsInRange", "GroupEnumUnitsInRangeOfLoc", "GroupEnumUnitsInRect", "GroupEnumUnitsOfPlayer", "GroupEnumUnitsOfType", "GroupEnumUnitsSelected", "ForceEnumPlayers", "ForceEnumAllies", "ForceEnumEnemies", "GetUnitsInRangeOfLocMatching", "GetUnitsInRectMatching", "GetUnitsOfPlayerMatching", "CountUnitsInGroup", "EnumItemsInRect", "EnumItemsInRectBJ", "EnumDestructablesInRect", "EnumDestructablesInRectAll", "EnumDestructablesInCircleBJ"}
MACRO = re.compile(r"YDLocal([1-6]?)(Array)?(Set|Get)$")


class Adapter:
    def __init__(self, text: str):
        self.text = text
        self.tokens = lex(text, include_directives=True)
        self.token_starts = [t.start for t in self.tokens]
        self.functions: dict[str, Function] = {}
        self.call_map: dict[int, Call] = {}
        self.symbols: dict[tuple[str, str, bool], int] = {}
        self.types: set[str] = set()
        self.registrations: list[tuple[Function, Call, str, str]] = []
        self.bridge_helpers: set[str] = set()
        self.captures: list[dict] = []
        self.control_paths = {}
        self.scope_offsets = [0]
        self.scope_paths: list[tuple[str, ...]] = [()]
        self.public_functions: dict[str, str] = {}
        self.named_pages: dict[str, int] = {}
        self.reference_cache: dict[int, list[tuple[str, int]]] = {}
        self.synchronous_callers: dict[str, set[str]] = {}
        self.all_callers: dict[str, set[str]] = {}
        self.trigger_actions: dict[str, list[tuple[Function, Call]]] = {}
        self.trusted_output = False

    def fail(self, message: str, pos: int = 0):
        raise TransformError(f"line {self.text.count(chr(10), 0, pos)+1}: {message}")

    def line_end(self, pos: int):
        n = self.text.find("\n", pos)
        return len(self.text) if n < 0 else n + 1

    def line_start(self, pos: int):
        return self.text.rfind("\n", 0, pos) + 1

    def code(self, span):
        return self.text[span[0]:span[1]].strip()

    def compact(self, text):
        return "".join(t.value for t in lex(text))

    def tokens_between(self, begin, end):
        return self.tokens[bisect_left(self.token_starts, begin):bisect_left(self.token_starts, end)]

    def scope_at(self, pos: int) -> tuple[str, ...]:
        return self.scope_paths[bisect_right(self.scope_offsets, pos) - 1]

    def resolve(self, name: str, pos: int) -> str:
        """Resolve a vJASS function without changing its emitted spelling."""
        scope = self.scope_at(pos)
        for size in range(len(scope), 0, -1):
            candidate = "::".join((*scope[:size], name))
            if candidate in self.functions:
                return candidate
        if name in self.functions:
            return name
        return self.public_functions.get(name, name)

    def control_path(self, fn, pos):
        """Lexical branch/loop identity, indexed once per affected function."""
        if fn.name not in self.control_paths:
            offsets, paths, stack = [fn.body - 1], [()], []
            tokens = self.tokens_between(fn.body, fn.end)
            def line_head(index):
                return index == 0 or self.line_end(tokens[index-1].end) <= tokens[index].start
            for index, token in enumerate(tokens):
                if token.kind != "id":
                    continue
                value = token.value
                cpp = index > 0 and tokens[index-1].value == "#" and line_head(index-1)
                if value in {"if", "ifdef", "ifndef", "else", "elif", "endif"} and cpp:
                    if value in {"if", "ifdef", "ifndef"}:
                        stack.append(("cpp_if", token.start))
                    elif value in {"else", "elif"}:
                        if not stack or not stack[-1][0].startswith("cpp_"):
                            self.fail("Malformed preprocessor conditional structure", token.start)
                        stack[-1] = ("cpp_" + value, token.start)
                    else:
                        if not stack or not stack[-1][0].startswith("cpp_"):
                            self.fail("Malformed preprocessor conditional structure", token.start)
                        stack.pop()
                elif not line_head(index) and not (value == "if" and index and tokens[index-1].value == "static" and line_head(index-1)):
                    # An include such as <YDTrigger/Common/loop.h> and a
                    # preprocessor replacement body are not JASS statements.
                    continue
                elif value in {"if", "loop"}:
                    stack.append((value, token.start))
                elif value in {"else", "elseif"}:
                    if not stack or stack[-1][0] not in {"if", "else", "elseif"}:
                        self.fail("Malformed conditional structure", token.start)
                    stack[-1] = (value, token.start)
                elif value in {"endif", "endloop"}:
                    if not stack or stack[-1][0].startswith("cpp_"):
                        self.fail("Malformed control-flow structure", token.start)
                    stack.pop()
                else:
                    continue
                offsets.append(token.start)
                paths.append(tuple(stack))
            self.control_paths[fn.name] = (offsets, paths)
        offsets, paths = self.control_paths[fn.name]
        return paths[bisect_left(offsets, pos) - 1]

    def parse(self, trusted_output=False):
        self.trusted_output = trusted_output
        ts = self.tokens
        scopes = []
        zinc = False
        for i, t in enumerate(ts):
            if not trusted_output and t.kind == "id" and (t.value == "YDLOC" or t.value.startswith("YDLOCAL_") or t.value.startswith("ydl_localvar_step")):
                self.fail("Direct old YDLOC/context-index access cannot be migrated safely", t.start)
            if not trusted_output and t.kind == "id" and t.value.lower().startswith("kklc_"):
                self.fail("Input already contains reserved KKLC symbols", t.start)
            if t.kind == "directive":
                zinc = t.value == "zinc"
                continue
            if zinc:
                continue
            # Namespace declarations are line statements, not occurrences in
            # strings, comments, member names, or function-reference expressions.
            first_on_line = i == 0 or self.line_end(ts[i-1].end) <= t.start
            if t.kind == "id" and first_on_line:
                if t.value in {"library", "library_once", "scope"} and i + 1 < len(ts):
                    scopes.append(("scope" if t.value == "scope" else "library", ts[i+1].value))
                    self.scope_offsets.append(t.start)
                    self.scope_paths.append(tuple(name for _, name in scopes))
                elif t.value in {"endlibrary", "endscope"}:
                    kind = "library" if t.value == "endlibrary" else "scope"
                    if not scopes or scopes[-1][0] != kind:
                        self.fail("Unmatched " + t.value, t.start)
                    scopes.pop()
                    self.scope_offsets.append(t.end)
                    self.scope_paths.append(tuple(name for _, name in scopes))
            if t.value == "function" and i + 2 < len(ts) and ts[i+2].value == "takes":
                ret = next((j for j in range(i+3, len(ts)) if ts[j].value == "returns"), None)
                end = next((j for j in range((ret or i)+2, len(ts)) if ts[j].value == "endfunction"), None)
                if ret is None or end is None:
                    self.fail("Incomplete function", t.start)
                source_name = ts[i+1].value
                visibility = ts[i-1].value if i and ts[i-1].value in {"private", "public"} and self.line_end(ts[i-1].end) > t.start else ""
                scope = tuple(name for _, name in scopes)
                name = "::".join((*scope, source_name)) if visibility and scope else source_name
                fn = Function(name, t.start, self.line_end(ts[ret+1].end), ts[end].start, ts[end].end, ts[i+3].start, ts[ret].start, ts[ret+1].value,
                              source_name=source_name, scope=scope, visibility=visibility)
                if fn.name in self.functions:
                    self.fail("Duplicate function " + source_name, t.start)
                self.functions[fn.name] = fn
                if visibility == "public" and scope:
                    alias = "_".join((*scope, source_name))
                    previous = self.public_functions.setdefault(alias, name)
                    if previous != name:
                        self.fail("Duplicate public function " + alias, t.start)
        if scopes:
            self.fail("Unclosed " + scopes[-1][0], len(self.text))
        for alias, name in self.public_functions.items():
            if alias in self.functions and alias != name:
                self.fail("Duplicate public function " + alias, self.functions[name].start)
        functions = list(self.functions.values())
        function_starts = [fn.body for fn in functions]
        # Balanced calls, including calls nested in another call's arguments.
        zinc = False
        for i, t in enumerate(ts[:-1]):
            if t.kind == "directive":
                zinc = t.value == "zinc"
                continue
            if zinc:
                continue
            if t.kind != "id" or ts[i+1].value != "(":
                continue
            if (i >= 2 and ts[i-1].value == "define" and ts[i-2].value == "#"
                    and (i == 2 or self.line_end(ts[i-3].end) <= ts[i-2].start)):
                # This is a preprocessor declaration header. Whitespace before
                # '(' distinguishes an object-like macro from a function-like
                # macro; neither header is a JASS call. Replacement-body calls
                # remain visible to the normal scan below.
                continue
            depth, begin, args = 1, ts[i+1].end, []
            for j in range(i+2, len(ts)):
                v = ts[j].value
                if ts[j].kind in {"string", "rawcode"}:
                    continue
                if v in {"(", "["}:
                    depth += 1
                elif v in {")", "]"}:
                    depth -= 1
                    if depth == 0:
                        if self.text[begin:ts[j].start].strip():
                            args.append((begin, ts[j].start))
                        # Member methods are not global functions, even when a
                        # method happens to share a native/helper function name.
                        name = "@member:" + t.value if i and ts[i-1].value == "." else self.resolve(t.value, t.start)
                        call = Call(name, t.start, ts[j].end, args, source_name=t.value)
                        self.call_map[t.start] = call
                        owner_index = bisect_right(function_starts, t.start) - 1
                        if owner_index >= 0 and t.start < functions[owner_index].end:
                            functions[owner_index].calls.append(call)
                        break
                elif v == "," and depth == 1:
                    args.append((begin, ts[j].start))
                    begin = ts[j].end
            else:
                self.fail("Unbalanced call " + t.value, t.start)
        for fn in functions:
            fn.call_starts = [call.start for call in fn.calls]
            for call in fn.calls:
                if call.name in self.functions:
                    self.synchronous_callers.setdefault(call.name, set()).add(fn.name)
                    self.all_callers.setdefault(call.name, set()).add(fn.name)
                for name in self.refs(call):
                    self.all_callers.setdefault(name, set()).add(fn.name)
                    if call.name in SYNC_CALLBACKS:
                        self.synchronous_callers.setdefault(name, set()).add(fn.name)
                if call.name == "TriggerAddAction" and call.args:
                    owner = self.compact(self.code(call.args[0]))
                    self.trigger_actions.setdefault(owner, []).append((fn, call))

    def macro(self, call: Call):
        m = MACRO.fullmatch(call.name)
        if not m:
            return None
        scope, array, op = m.groups()
        offset = 1 if not scope or scope == "6" else 0
        count = offset + 2 + bool(array) + (op == "Set")
        if len(call.args) != count:
            self.fail("Unexpected arguments for " + call.name, call.start)
        if scope == "6":
            self.named_page(call)
        typ = self.code(call.args[offset])
        if typ not in TYPE_BASE:
            self.fail("Unsupported type mapping " + typ, call.start)
        name_tokens = lex(self.code(call.args[offset+1]))
        if len(name_tokens) != 1 or name_tokens[0].kind != "string":
            self.fail("Local symbol name must be a literal", call.start)
        # Literal spelling is the identity; reject escape-equivalent spellings.
        name = name_tokens[0].value[1:-1]
        if "\\" in name:
            self.fail("Escaped local symbol names require explicit migration", call.start)
        # GUI semantic aliases use the same native storage bucket (e.g.
        # degree/real, unitcode/integer). Keep those read/write identities equal.
        typ = TYPE_BASE[typ]
        key = (typ, name, bool(array))
        self.symbols.setdefault(key, len(self.symbols) + 1)
        self.types.add(typ)
        return scope, bool(array), op, offset, key

    def named_page(self, call: Call) -> int:
        tokens = lex(self.code(call.args[0])) if call.args else []
        if len(tokens) != 1 or tokens[0].kind != "string":
            self.fail("Named local page must be a literal", call.start)
        name = tokens[0].value[1:-1]
        if "\\" in name:
            self.fail("Escaped local page names require explicit migration", call.start)
        return self.named_pages.setdefault(name, len(self.named_pages) + 1)

    def references(self, call: Call):
        if call.start in self.reference_cache:
            return self.reference_cache[call.start]
        refs = []
        for begin, end in call.args:
            tokens = lex(self.text[begin:end])
            refs.extend((self.resolve(tokens[i+1].value, call.start), begin + tokens[i+1].start) for i, token in enumerate(tokens[:-1])
                        if token.kind == "id" and token.value == "function" and tokens[i+1].kind == "id")
        self.reference_cache[call.start] = refs
        return refs

    def refs(self, call: Call):
        return [name for name, _ in self.references(call)]

    def check_dynamic_conditions(self):
        """Reject conditions/actions that can alter a dynamic body's dispatch.

        Track straight-line owner aliases and parameter forwarding. Conditional
        assignments retain both possible identities; ambiguity is rejected rather
        than assuming that a branch ran. This does not attempt whole-program JASS
        value analysis or infer opaque external helper effects.
        """
        dynamic = [r for r in self.registrations if self.functions[r[2]].role == "dynamic"]
        if not dynamic:
            return
        assignments, locals_, parameters, local_handles = {}, {}, {}, {}
        for fn in self.functions.values():
            params = self.compact(self.text[fn.takes_start:fn.takes_end])
            pairs = self.text[fn.takes_start:fn.takes_end].strip().split(",") if params != "nothing" else []
            parameters[fn.name] = {lex(pair)[1].value: i for i, pair in enumerate(pairs)}
            locals_[fn.name] = set(parameters[fn.name])
            assignments[fn.name] = {}
            local_handles[fn.name] = {}
            for call in fn.calls:
                if self.trusted_output and call.name == "KKLC_Set_trigger" and len(call.args) == 3:
                    context = self.compact(self.code(call.args[0]))
                    symbol = self.compact(self.code(call.args[1]))
                    if context == "kklc_ctx" and re.fullmatch(r"[1-9]\d*", symbol):
                        local_handles[fn.name].setdefault(("generated_trigger", context, symbol), []).append(call)
                    continue
                if call.name not in {"YDLocal1Set", "YDLocal2Set"}:
                    continue
                macro = self.macro(call)
                if macro and macro[4][0] == "trigger":
                    local_handles[fn.name].setdefault(macro[4], []).append(call)
            tokens = self.tokens_between(fn.body, fn.end)
            for i, token in enumerate(tokens[:-2]):
                if token.value == "local":
                    locals_[fn.name].add(tokens[i+2].value)
                    j = i + 2
                elif token.value == "set":
                    j = i + 1
                else:
                    continue
                if j + 2 < len(tokens) and tokens[j+1].value == "=":
                    assignments[fn.name].setdefault(tokens[j].value, []).append(
                        (token.start, tokens[j+2].start, self.line_end(token.start)))

        cache = {}
        def identities(fn, expression, pos):
            name = self.compact(expression)
            key = (fn.name, name, pos)
            if key in cache:
                return cache[key]
            # null carries no owner identity. Treating it as a global symbol
            # joins every independently destroyed/cleared global handle into
            # one alias class through their ordinary cleanup assignments.
            if name == "null":
                return frozenset()
            if name in {"GetExpiredTimer()", "GetTriggeringTrigger()"}:
                return frozenset({("native", fn.name, name)})
            local_get = re.fullmatch(r'YDLocal[12]Get\(trigger,("[^"\\]*")\)', name)
            generated_get = (re.fullmatch(r'LoadTriggerHandle\(KKLC_Data,(kklc_ctx),([1-9]\d*)\)', name)
                             if self.trusted_output else None)
            if local_get or generated_get:
                symbol = (("trigger", local_get[1][1:-1], False) if local_get else
                          ("generated_trigger", generated_get[1], generated_get[2]))
                previous = [call for call in local_handles[fn.name].get(symbol, []) if call.start < pos]
                if previous:
                    call = previous[-1]
                    result = identities(fn, self.code(call.args[-1]), call.start)
                    branch = self.control_path(fn, call.start)
                    if self.control_path(fn, pos)[:len(branch)] != branch:
                        result |= identities(fn, expression, call.start)
                    cache[key] = result
                    return result
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                return frozenset({("expression", fn.name, name, pos)})
            previous = [a for a in assignments[fn.name].get(name, []) if a[0] < pos]
            if previous:
                start, begin, end = previous[-1]
                result = identities(fn, self.text[begin:end], start)
                branch = self.control_path(fn, start)
                if self.control_path(fn, pos)[:len(branch)] != branch:
                    result |= identities(fn, name, start)
            elif name in parameters[fn.name]:
                result = frozenset({("parameter", fn.name, parameters[fn.name][name])})
            elif name in locals_[fn.name]:
                result = frozenset({("local", fn.name, name)})
            else:
                result = frozenset({("global", name)})
            cache[key] = result
            return result

        # A global alias is observable from a different function. Keep the
        # alias's possible identities without conflating reused local variables.
        global_aliases = []
        for fn in self.functions.values():
            for name, values in assignments[fn.name].items():
                if name not in locals_[fn.name]:
                    for start, begin, end in values:
                        global_aliases.append((frozenset({("global", name)}), identities(fn, self.text[begin:end], start)))
        alias_graph = {}
        for left, right in global_aliases:
            joined = left | right
            if joined:
                anchor = next(iter(joined))
                for value in joined:
                    alias_graph.setdefault(anchor, set()).add(value)
                    alias_graph.setdefault(value, set()).add(anchor)
        expansion_cache = {}
        def expand(values):
            values = frozenset(values)
            if values in expansion_cache:
                return expansion_cache[values]
            result = set(values)
            pending = list(values)
            while pending:
                for value in alias_graph.get(pending.pop(), ()):
                    if value not in result:
                        result.add(value)
                        pending.append(value)
            result = frozenset(result)
            expansion_cache[values] = result
            return result

        effects = {fn.name: set() for fn in self.functions.values()}
        origins = {}
        for fn in self.functions.values():
            for call in fn.calls:
                if call.name in {"TriggerAddCondition", "TriggerAddAction"} and call.args:
                    origins[call.start] = call
                    for owner in identities(fn, self.code(call.args[0]), call.start):
                        effects[fn.name].add((call.name, owner, call.start))
        changed = True
        while changed:
            changed = False
            for fn in self.functions.values():
                added = set()
                for call in fn.calls:
                    if call.name not in effects:
                        continue
                    for kind, owner, origin in effects[call.name]:
                        if owner[:2] == ("parameter", call.name):
                            index = owner[2]
                            if index >= len(call.args):
                                continue
                            targets = identities(fn, self.code(call.args[index]), call.start)
                        else:
                            targets = {owner}
                        added.update((kind, target, origin) for target in targets)
                old = len(effects[fn.name])
                effects[fn.name].update(added)
                changed |= old != len(effects[fn.name])

        global_effects = {effect for values in effects.values() for effect in values
                          if any(item[0] == "global" for item in expand({effect[1]}))}

        for fn, registration, cbname, owner in dynamic:
            expression = lex(self.code(registration.args[1]))
            if (len(expression) != 5 or [t.value for t in expression[:3]] != ["Condition", "(", "function"]
                    or expression[4].value != ")" or self.resolve(expression[3].value, registration.start) != cbname):
                self.fail("Composite dynamic conditions require explicit migration", registration.start)
            source = identities(fn, owner, registration.start)
            if any(item[0] == "parameter" for item in source):
                self.fail("Dynamic registration through a parameter owner requires explicit migration", registration.start)
            if any(item[0] == "expression" and item[2] != "CreateTrigger()" for item in source):
                self.fail("Dynamic owner has an opaque expression or getter source", registration.start)
            target = expand(source)
            candidates = effects[fn.name] | global_effects
            for kind, candidate, origin in candidates:
                if origin == registration.start or not target & expand({candidate}):
                    continue
                if kind == "TriggerAddAction":
                    self.fail("Mixed dynamic condition and ordinary action on the same trigger or alias", origins[origin].start)
                self.fail("Additional condition on a dynamic trigger or alias", origins[origin].start)

    def analyze(self):
        allowed = {"YDLocalInitialize", "YDLocalReset", "YDLocalExecuteTrigger", "YDLocal1Release", "YDLocal3Release", "YDLocal4Release", "YDLocal6Release", "YDLocalRelease"}
        for fn in self.functions.values():
            for c in fn.calls:
                m = self.macro(c)
                if m:
                    scope, array, op, off, key = m
                    fn.symbols.add(key)
                    (fn.reads if op == "Get" else fn.writes).add(key)
                    if scope == "1":
                        fn.role = "root"
                    elif scope == "2" and not fn.role:
                        fn.role = "helper"
                    elif scope in {"3", "4"}:
                        fn.role = "timer" if scope == "3" else "dynamic"
                    elif not scope:
                        owner = self.compact(self.code(c.args[0]))
                        if owner == "GetExpiredTimer()":
                            fn.role = "timer"
                        elif owner == "GetTriggeringTrigger()":
                            fn.role = "dynamic"
                elif c.name.startswith("YDLocal") and c.name not in allowed:
                    self.fail("Unknown local macro " + c.name, c.start)
                elif c.name in allowed:
                    expected = 1 if c.name in {"YDLocalExecuteTrigger", "YDLocalRelease", "YDLocal6Release"} else 0
                    if len(c.args) != expected:
                        self.fail("Unexpected arguments for " + c.name, c.start)
                    if c.name == "YDLocal6Release":
                        self.named_page(c)
                if c.name == "YDLocalInitialize":
                    if fn.role not in {"", "root", "helper"}:
                        self.fail("Conflicting root/callback context", c.start)
                    fn.role = "root"
        # A generated callback can consist only of a synchronous enumeration or
        # helper call. Its owner-dependent macros then live in a descendant,
        # rather than directly in the function registered with the engine.
        owner_requirements = {fn.name: ({fn.role} if fn.role in {"timer", "dynamic"} else set())
                              for fn in self.functions.values()}
        changed = True
        while changed:
            changed = False
            for fn in self.functions.values():
                required = owner_requirements[fn.name]
                old = len(required)
                for call in fn.calls:
                    for name in [call.name] + (self.refs(call) if call.name in SYNC_CALLBACKS else []):
                        required.update(owner_requirements.get(name, ()))
                changed |= old != len(required)
        for fn in self.functions.values():
            if not fn.role and owner_requirements[fn.name]:
                if len(owner_requirements[fn.name]) != 1:
                    self.fail("Synchronous callback mixes timer and trigger owner contexts", fn.start)
                fn.role = next(iter(owner_requirements[fn.name]))
        # Establish timer/dynamic callback ownership from actual registration.
        for fn in self.functions.values():
            for c in fn.calls:
                refs = self.refs(c)
                if c.name == "TimerStart" and len(c.args) == 4 and refs:
                    target = self.code(c.args[0]); role = "timer"
                elif c.name == "TriggerAddCondition" and len(c.args) == 2 and refs:
                    target = self.code(c.args[0]); role = "dynamic"
                else:
                    continue
                for name in refs:
                    cb = self.functions.get(name)
                    if cb and cb.role == "root":
                        self.fail("Root local macros used as a timer/condition callback", c.start)
                    # Preparing another trigger's parameter frame does not make
                    # this callback an ordinary trigger-action root. Its own
                    # frame follows the proven timer/condition registration.
                    if cb and not cb.role and any(call.name == "YDLocalExecuteTrigger" for call in cb.calls):
                        cb.role = role
                    if cb and cb.role in {"timer", "dynamic"}:
                        if cb.role != role:
                            self.fail("Callback registered with incompatible owner", c.start)
                        if not re.fullmatch(r"[A-Za-z_]\w*|GetExpiredTimer\(\)|GetTriggeringTrigger\(\)", self.compact(target)):
                            self.fail("Callback owner must be a stable handle variable", c.start)
                        self.registrations.append((fn, c, name, target))
        registered = {x[2] for x in self.registrations}
        self.check_dynamic_conditions()
        for parent, registration, cbname, owner in self.registrations:
            if self.functions[cbname].role != "dynamic":
                continue
            canonical = self.compact(owner)
            # Do not turn a conventional trigger with actions into a
            # condition-only dynamic dispatch. Reused local variables are
            # bounded by assignments on either side of this registration.
            parent_tokens = self.tokens_between(parent.body, parent.end)
            assignments = [t.start for i,t in enumerate(parent_tokens[:-2]) if t.value == "set" and parent_tokens[i+1].value == canonical and parent_tokens[i+2].value == "="]
            lower = max([parent.body] + [p for p in assignments if p < registration.start])
            upper = min([parent.end] + [p for p in assignments if p > registration.start])
            for fn, c in self.trigger_actions.get(canonical, ()):
                same_segment = fn is parent and lower <= c.start < upper
                global_owner = canonical.startswith(("gg_", "udg_"))
                if same_segment or global_owner:
                    self.fail("Mixed dynamic condition and ordinary action on the same trigger", c.start)
        for fn in self.functions.values():
            if fn.role in {"timer", "dynamic"}:
                fn.owner_role = fn.role
        # Generated helpers inside a timer/dynamic callback inherit that frame;
        # their native GetExpiredTimer/GetTriggeringTrigger reads are not another
        # independent persistent owner entry.
        progress = True
        while progress:
            progress = False
            for fn in self.functions.values():
                if fn.role not in {"timer", "dynamic"} or fn.name in registered:
                    continue
                parents = [self.functions[name] for name in self.synchronous_callers.get(fn.name, ())]
                if parents and all(p.owner_role == fn.owner_role for p in parents):
                    fn.role = "helper"; progress = True
        for fn in self.functions.values():
            if fn.role in {"timer", "dynamic"} and fn.name not in registered:
                self.fail("Cannot establish callback registration for " + fn.name, fn.start)
        # Propagate explicit context through ordinary synchronous helper calls.
        changed = True
        while changed:
            changed = False
            for fn in self.functions.values():
                if not fn.role and any(self.functions.get(name) and self.functions[name].role == "helper"
                                       for c in fn.calls
                                       for name in ([c.name] + (self.refs(c) if c.name in SYNC_CALLBACKS else []))):
                    fn.role = "helper"; changed = True
        unrecognized_refs = []
        seen_references = set()
        for fn in self.functions.values():
            if fn.role == "helper" and self.compact(self.text[fn.takes_start:fn.takes_end]) != "nothing":
                self.fail("Helper with existing parameters is unsupported: " + fn.name, fn.start)
            for c in fn.calls:
                if c.name == "ExecuteFunc" and c.args:
                    name_tokens = lex(self.code(c.args[0]))
                    if len(name_tokens) == 1 and name_tokens[0].kind == "string":
                        target = self.functions.get(self.resolve(name_tokens[0].value[1:-1], c.start))
                        if target and target.role == "helper":
                            self.fail("ExecuteFunc cannot supply an explicit helper context", c.start)
                    elif any(target.role == "helper" for target in self.functions.values()):
                        self.fail("Computed ExecuteFunc target cannot prove an explicit helper context", c.start)
                for ref, pos in self.references(c):
                    cb = self.functions.get(ref)
                    if not cb or cb.role != "helper":
                        continue
                    if pos in seen_references:
                        continue
                    seen_references.add(pos)
                    containers = sorted((o for o in fn.calls if o.start <= pos < o.end), key=lambda o: o.end - o.start)
                    supported = False
                    for outer in containers:
                        if outer.name in SYNC_CALLBACKS:
                            supported = True
                            break
                        if outer.name not in {"Filter", "Condition", "And", "Or", "Not"}:
                            break
                    if not supported:
                        unrecognized_refs.append((ref, pos))
                        continue
                    self.bridge_helpers.add(ref)
            if fn.role and fn.returns not in set(TYPE_BASE.values()) | {"nothing"}:
                self.fail("Unsupported function result type " + fn.returns, fn.start)
        if unrecognized_refs:
            ref, pos = unrecognized_refs[0]
            extra = "; ".join(f"line {self.text.count(chr(10), 0, p) + 1}: {name}" for name, p in unrecognized_refs[1:12])
            self.fail("Unrecognized or asynchronous helper code reference " + ref + ("; additional: " + extra if extra else ""), pos)
        # Bare YDLocal tokens (e.g. in directives) must not evade call analysis.
        for t in self.tokens:
            if t.kind == "id" and t.value.startswith("YDLocal") and t.start not in self.call_map:
                self.fail("Unrecognized local token " + t.value, t.start)
        for fn in self.functions.values():
            if fn.role == "helper":
                incoming = any(name != fn.name for name in self.all_callers.get(fn.name, ()))
                if not incoming:
                    self.fail("Helper has no proven context caller: " + fn.name, fn.start)
            if any((m := self.macro(c)) and m[0] == "5" for c in fn.calls) and not any(c.name == "YDLocalExecuteTrigger" for c in fn.calls):
                self.fail("Parameter local used without a prepared frame", fn.start)
            for c in fn.calls:
                m = self.macro(c)
                if m:
                    if m[0] in {"1", "2", "3", "4"} or (not m[0] and self.owner_context(fn, self.code(c.args[0])) == "kklc_ctx"):
                        fn.frame_symbols.add(m[4])
                if c.name == "YDLocal1Release":
                    # The observed generator emits this only in epilogues (then
                    # optional handle-null cleanup and a return). A handwritten
                    # mid-body Flush is a different semantic operation.
                    suffix = self.text[c.end:fn.end]
                    for line, tokens in token_lines(suffix):
                        if not tokens:
                            continue
                        if tokens[0].value == "return":
                            break
                        if len(tokens) == 4 and tokens[0].value == "set" and tokens[2].value == "=" and tokens[3].value == "null":
                            continue
                        self.fail("Mid-body YDLocal1Release requires explicit clear semantics", c.start)
        # Compute synchronous helper effects, including reads in nested filters.
        changed = True
        while changed:
            changed = False
            for fn in self.functions.values():
                for c in fn.calls:
                    names = [c.name] + self.refs(c)
                    for name in names:
                        helper = self.functions.get(name)
                        if not helper or helper.role != "helper":
                            continue
                        old = (len(fn.symbols), len(fn.reads), len(fn.writes), len(fn.frame_symbols))
                        fn.symbols |= helper.symbols
                        fn.reads |= helper.reads
                        fn.writes |= helper.writes
                        fn.frame_symbols |= helper.frame_symbols
                        changed |= old != (len(fn.symbols), len(fn.reads), len(fn.writes), len(fn.frame_symbols))
        self.can_wait = {fn.name for fn in self.functions.values() if any(c.name in {"TriggerSleepAction", "PolledWait", "TriggerWaitForSound", "WaitForSoundBJ"} for c in fn.calls)}
        changed = True
        while changed:
            changed = False
            for fn in self.functions.values():
                if fn.name not in self.can_wait and any(c.name in self.can_wait for c in fn.calls):
                    self.can_wait.add(fn.name); changed = True
        for name in self.bridge_helpers & self.can_wait:
            self.fail("Wait in synchronous callback bridge: " + name, self.functions[name].start)
        for fn in self.functions.values():
            if fn.role == "dynamic" and fn.name in self.can_wait:
                self.fail("Wait in dynamic Condition callback: " + fn.name, fn.start)

    def owner_context(self, fn, owner):
        compact = self.compact(owner)
        if compact == "GetExpiredTimer()" and (fn.role == "timer" or fn.owner_role == "timer"):
            return "kklc_ctx"
        if compact == "GetTriggeringTrigger()" and (fn.role == "dynamic" or fn.owner_role == "dynamic"):
            return "kklc_ctx"
        return f"KKLC_Owner({owner})"

    def render(self, fn, begin, end):
        first = bisect_left(fn.call_starts, begin)
        last = bisect_left(fn.call_starts, end)
        candidates = [c for c in fn.calls[first:last] if c.end <= end]
        out, cursor = [], begin
        for c in sorted(candidates, key=lambda c: (c.start, -c.end)):
            if c.start < cursor:
                continue
            out.append(self.text[cursor:c.start])
            out.append(self.render_call(fn, c))
            cursor = c.end
        out.append(self.text[cursor:end])
        return "".join(out)

    def render_call(self, fn, c):
        # Preserve newlines terminating line comments within an argument.
        args = [self.render(fn, a, b).strip(" \t\r") for a, b in c.args]
        m = self.macro(c)
        if m:
            scope, array, op, off, key = m
            typ, _, _ = key
            ctx = (f"KKLC_NamedOwner({self.named_page(c)})" if scope == "6" else
                   "ydl_triggerstep" if scope == "5" else (self.owner_context(fn, args[0]) if not scope else "kklc_ctx"))
            if scope in {"1", "2", "3", "4"} and not fn.role:
                self.fail("Local context is unavailable", c.start)
            symbol = self.symbols[key]
            if op == "Set":
                if array:
                    return f"KKLC_SetArray_{typ}({ctx}, {symbol}, {args[off+2]}, {args[off+3]})"
                return f"KKLC_Set_{typ}({ctx}, {symbol}, {args[off+2]})"
            if array:
                return f"YDHashGet(KKLC_Arrays, {typ}, KKLC_Array({ctx}, {symbol}, false), {args[off+2]})"
            return f"YDHashGet(KKLC_Data, {typ}, {ctx}, {symbol})"
        if c.name == "YDLocalExecuteTrigger":
            if len(args) != 1:
                self.fail("Invalid parameter frame", c.start)
            # exitwhen may skip an earlier dispatch. Retire the still-owned
            # pending frame before replacing this function-local slot.
            return f"call KKLC_Release(ydl_triggerstep)\n    set ydl_triggerstep = KKLC_Prepare({args[0]})"
        if c.name == "YDTriggerExecuteTrigger":
            if len(args) != 2:
                self.fail("Invalid trigger dispatch", c.start)
            return f"KKLC_Execute({args[0]}, {args[1]}, ydl_triggerstep)"
        if c.name == "YDLocalReset":
            return "DoNothing()"
        if c.name in {"YDLocalInitialize", "YDLocal1Release"}:
            return "DoNothing()"
        if c.name == "YDLocal3Release":
            return "KKLC_ClearContext(kklc_ctx)" if fn.role == "timer" or fn.owner_role == "timer" else "KKLC_ClearOwner(GetExpiredTimer())"
        if c.name == "YDLocal4Release":
            return "KKLC_ClearContext(kklc_ctx)" if fn.role == "dynamic" or fn.owner_role == "dynamic" else "KKLC_ClearOwner(GetTriggeringTrigger())"
        if c.name == "YDLocalRelease":
            return f"KKLC_ClearOwner({args[0]})"
        if c.name == "YDLocal6Release":
            return f"KKLC_ClearContext(KKLC_NamedOwner({self.named_page(c)}))"
        if c.name in {"DestroyTimer", "DestroyTrigger"}:
            return f"KKLC_{c.name}({', '.join(args)})"
        if c.name in self.functions and self.functions[c.name].role == "helper":
            if args:
                self.fail("Unexpected helper arguments", c.start)
            if not fn.role:
                self.fail("Helper call without context", c.start)
            return c.source_name + "(kklc_ctx)"
        rendered = self.text[c.start:c.end]
        if args:
            rendered = c.source_name + "(" + ", ".join(args) + ")"
        # Rewrite references only in whitelisted synchronous call expressions.
        if c.name in SYNC_CALLBACKS:
            toks = lex(rendered)
            edits = []
            for i, t in enumerate(toks[:-1]):
                if t.value == "function" and self.resolve(toks[i+1].value, c.start) in self.bridge_helpers:
                    target = toks[i+1]
                    edits.append((target.start, target.end, target.value + "_KKLCBridge"))
            for a, b, value in reversed(edits):
                rendered = rendered[:a] + value + rendered[b:]
        return rendered

    def default(self, result):
        return {"nothing": "", "integer": "0", "real": "0.0", "boolean": "false", "string": '""'}.get(result, "null")

    def function_output(self, fn):
        text = self.text
        insert_before: dict[int, list[str]] = {}
        insert_after: dict[int, list[str]] = {}
        def before(pos, value): insert_before.setdefault(pos, []).append(value)
        def after(pos, value): insert_after.setdefault(pos, []).append(value)
        # Capture only fields read by the child, absent in explicit owner parameters,
        # and present in the parent frame. Scalar and array namespaces stay distinct.
        for parent, call, cbname, target in self.registrations:
            if parent is not fn:
                continue
            cb = self.functions[cbname]
            assignment = None
            assignment_start = None
            ft = self.tokens_between(fn.body, call.start)
            for i,t in enumerate(ft[:-2]):
                if t.value == "set" and ft[i+1].value == self.compact(target) and ft[i+2].value == "=":
                    assignment = self.line_end(t.start)
                    assignment_start = t.start
                elif t.value == "local" and i + 3 < len(ft) and ft[i+2].value == self.compact(target) and ft[i+3].value == "=":
                    # Actions cannot be inserted between local declarations.
                    assignment = self.line_end(t.start)
                    assignment_start = t.start
                    for line, line_tokens in token_lines(self.text[assignment:fn.end]):
                        if line_tokens and line_tokens[0].value != "local":
                            break
                        assignment += len(line)
            segment = assignment if assignment is not None else fn.body
            if cb.role == "dynamic":
                before(self.line_start(call.start), f"    call KKLC_MarkDynamic({target})\n")
            prior = [c for c in fn.calls if segment <= c.start < call.start and (m := self.macro(c)) and not m[0] and m[2] == "Set" and self.compact(self.code(c.args[0])) == self.compact(target)]
            registration_path = self.control_path(fn, call.start)
            if assignment_start is not None:
                assignment_path = self.control_path(fn, assignment_start)
                if registration_path[:len(assignment_path)] != assignment_path:
                    self.fail("Owner assignment crosses a conditional or loop boundary", call.start)
            for explicit_write in prior:
                write_path = self.control_path(fn, explicit_write.start)
                if registration_path[:len(write_path)] != write_path:
                    self.fail("Explicit capture parameters cross a conditional or loop boundary", explicit_write.start)
            explicit = {self.macro(c)[4] for c in prior}
            parent_local = fn.frame_symbols
            for key in sorted(cb.reads):
                if key not in parent_local:
                    continue
                if key in explicit and not key[2]:
                    continue
                if not fn.role:
                    self.fail("Automatic capture has no parent context", call.start)
                typ, name, array = key
                sym = self.symbols[key]
                dest = self.owner_context(fn, target)
                capture_at = self.line_start(call.start)
                if array:
                    writes = [c for c in prior if self.macro(c)[4] == key]
                    if writes:
                        if assignment is None:
                            self.fail("Cannot bound explicit array parameters to an owner assignment", call.start)
                        capture_at = self.line_start(writes[0].start)
                        # Capturing at the first explicit override avoids
                        # overwriting that override. Later parent writes would
                        # make the intended snapshot ambiguous; do not guess.
                        if any(capture_at < c.start < call.start and (m := self.macro(c)) and m[2] == "Set" and m[4] == key and (m[0] in {"1", "2", "3", "4"} or (not m[0] and self.owner_context(fn, self.code(c.args[0])) == "kklc_ctx")) for c in fn.calls):
                            self.fail("Parent array changes after explicit capture parameters", call.start)
                    code = f"    call KKLC_CopyArray_{typ}(kklc_ctx, {dest}, {sym})\n"
                else:
                    code = f"    call KKLC_Set_{typ}({dest}, {sym}, YDHashGet(KKLC_Data, {typ}, kklc_ctx, {sym}))\n"
                before(capture_at, code)
                self.captures.append({"parent": fn.name, "callback": cbname, "name": name, "type": typ, "array": array})
        # A bridge is held only for the synchronous native statement itself.
        bridge_lines = set()
        for c in fn.calls:
            if c.name in SYNC_CALLBACKS and any(ref in self.bridge_helpers for ref in self.refs(c)):
                a, b = self.line_start(c.start), self.line_end(c.end)
                prefix = self.compact(text[a:c.start])
                if not (prefix.startswith("call") or prefix.startswith("set")):
                    self.fail("Synchronous callback must be in a call/set statement", c.start)
                if not fn.role:
                    self.fail("Callback bridge has no context", c.start)
                if a not in bridge_lines:
                    before(a, "    set kklc_bridge = KKLC_BridgePush(kklc_ctx)\n")
                    # A failed push leaves the outer bridge intact. Dispatching
                    # anyway would let the callback borrow that unrelated frame.
                    # Abort this entry and release only its owned references.
                    failed = "    if kklc_bridge == 0 then\n"
                    if any(call.name == "YDLocalExecuteTrigger" for call in fn.calls):
                        failed += "        call KKLC_Release(ydl_triggerstep)\n"
                    failed += "        call KKLC_Release(kklc_ctx)\n        return"
                    if fn.returns != "nothing":
                        failed += " " + self.default(fn.returns)
                    failed += "\n    endif\n"
                    before(a, failed)
                    after(b, "    call KKLC_BridgePop(kklc_bridge)\n")
                    bridge_lines.add(a)
        body_tokens = self.tokens_between(fn.body, fn.end)
        prepared = [c for c in fn.calls if c.name == "YDLocalExecuteTrigger"]
        dispatched = [c for c in fn.calls if c.name == "YDTriggerExecuteTrigger"]
        if len(prepared) != len(dispatched):
            self.fail("Unpaired parameter preparation/dispatch", fn.start)
        if prepared:
            declarations = {body_tokens[i+2].start for i, token in enumerate(body_tokens[:-2])
                            if token.value == "local" and body_tokens[i+1].value == "integer"
                            and body_tokens[i+2].value == "ydl_triggerstep"}
            if len(declarations) > 1:
                self.fail("Duplicate parameter-frame local", fn.start)
            for token in body_tokens:
                if token.kind == "id" and token.value == "ydl_triggerstep" and token.start not in declarations:
                    self.fail("Direct parameter-frame local access is unsupported", token.start)
            sequence = sorted(prepared + dispatched, key=lambda c: c.start)
            if any(c.name != ("YDLocalExecuteTrigger" if i % 2 == 0 else "YDTriggerExecuteTrigger") for i,c in enumerate(sequence)):
                self.fail("Nested parameter frames require explicit structural migration", fn.start)
            for p, d in zip(prepared, dispatched):
                if self.compact(text[self.line_start(p.start):p.start]):
                    self.fail("Parameter preparation must be a standalone macro statement", p.start)
                if p.start > d.start:
                    self.fail("Invalid parameter frame order", p.start)
                if self.compact(self.code(p.args[0])) != self.compact(self.code(d.args[0])):
                    self.fail("Prepared and dispatched trigger differ", p.start)
                owner_name = self.compact(self.code(p.args[0]))
                if not re.fullmatch(r"[A-Za-z_]\w*", owner_name):
                    self.fail("Prepared trigger must be a stable variable", p.start)
                for i,t in enumerate(body_tokens[:-2]):
                    if p.start < t.start < d.start and t.value == "set" and body_tokens[i+1].value == owner_name and body_tokens[i+2].value == "=":
                        self.fail("Prepared trigger variable is reassigned before dispatch", t.start)
                if self.control_path(fn, p.start) != self.control_path(fn, d.start):
                    self.fail("Prepare/dispatch cross a conditional or loop boundary", p.start)
                if any(c.start > p.start and c.start < d.start and (c.name in {"TriggerSleepAction", "PolledWait"} or c.name in self.can_wait) for c in fn.calls):
                    self.fail("Wait inside parameter preparation", p.start)
                after(self.line_end(d.end), "    set ydl_triggerstep = 0\n")
            for call in fn.calls:
                macro = self.macro(call)
                if macro and macro[0] == "5" and not any(p.end <= call.start < d.start for p, d in zip(prepared, dispatched)):
                    self.fail("Parameter local accessed outside preparation/dispatch", call.start)
        # Transform return expressions before releasing the owned frame.
        replacements = []
        if prepared:
            for i,t in enumerate(body_tokens[:-2]):
                if t.value == "local" and body_tokens[i+1].value == "integer" and body_tokens[i+2].value == "ydl_triggerstep":
                    end = self.line_end(body_tokens[i+2].end)
                    tail = self.compact(self.text[body_tokens[i+2].end:end])
                    if tail not in {"", "=0"}:
                        self.fail("Parameter-frame local has an unknown initializer", t.start)
                    replacements.append((t.start, end, "local integer ydl_triggerstep = 0\n"))
        for t in body_tokens:
            if t.value != "return" or not fn.role:
                continue
            end = self.line_end(t.end)
            expr = self.render(fn, t.end, end).strip()
            if expr.startswith("//"):
                expr = ""
            statement = ""
            if fn.returns != "nothing":
                if not expr:
                    self.fail("Missing return value", t.start)
                statement += f"set kklc_result = {expr}\n    "
            if prepared:
                statement += "call KKLC_Release(ydl_triggerstep)\n    "
            statement += "call KKLC_Release(kklc_ctx)\n    return"
            if fn.returns != "nothing":
                statement += " kklc_result"
            replacements.append((t.start, end, statement + "\n"))
        # Render non-overlapping statements and special return spans.
        boundaries = sorted({fn.body, fn.end, *insert_before, *insert_after, *(a for a,b,v in replacements), *(b for a,b,v in replacements)})
        output = []
        repl = {a: (b,v) for a,b,v in replacements}
        cursor = fn.body
        for pos in boundaries:
            if pos < cursor:
                continue
            if pos > cursor:
                output.append(self.render(fn, cursor, pos))
                cursor = pos
            output.extend(insert_after.get(pos, []))
            output.extend(insert_before.get(pos, []))
            if pos in repl:
                end, value = repl[pos]; output.append(value); cursor = end
        if cursor < fn.end:
            output.append(self.render(fn, cursor, fn.end))
        body = "".join(output)
        # Initialize macro was a naked macro statement, not a JASS call.
        body = re.sub(r"(?m)^(\s*)DoNothing\(\)\s*$", r"\1call DoNothing()", body)
        header = text[fn.start:fn.body]
        if fn.role == "helper":
            header = text[fn.start:fn.takes_start] + "integer kklc_parent " + text[fn.takes_end:fn.body]
        decl = ""
        if fn.role:
            entry = {"root": "KKLC_EnterRoot(GetTriggeringTrigger())", "timer": "KKLC_AcquireOwner(GetExpiredTimer())", "dynamic": "KKLC_AcquireOwner(GetTriggeringTrigger())", "helper": "KKLC_Retain(kklc_parent)"}[fn.role]
            decl += f"    local integer kklc_ctx = {entry}\n"
            if fn.returns != "nothing":
                decl += f"    local {fn.returns} kklc_result\n"
        if bridge_lines:
            decl += "    local integer kklc_bridge = 0\n"
        if prepared and not re.search(r"\blocal\s+integer\s+ydl_triggerstep\b", " ".join(t.value for t in body_tokens)):
            decl += "    local integer ydl_triggerstep = 0\n"
        # Place allocation failure guard after original local declarations.
        if fn.role:
            # Source declarations are one JASS statement per line (expressions may
            # contain nested calls). Find the first non-local source statement.
            insertion = 0
            for line, clean in token_lines(body):
                if clean and clean[0].value != "local":
                    break
                insertion += len(line)
            guard = "    if kklc_ctx == 0 then\n        return" + (" " + self.default(fn.returns) if fn.returns != "nothing" else "") + "\n    endif\n"
            body = body[:insertion] + guard + body[insertion:]
            if prepared:
                body += "    call KKLC_Release(ydl_triggerstep)\n"
            body += "    call KKLC_Release(kklc_ctx)\n"
        return header + decl + body + "endfunction"

    def wrappers(self):
        out = ["// KKLC adapter v1 generated typed accessors\n"]
        for typ in sorted(self.types):
            base = TYPE_BASE[typ]
            null = typ in HANDLES
            out += [f"function KKLC_Set_{typ} takes integer ctx, integer symbol, {base} value returns nothing\n", "    if not KKLC_Alive(ctx) then\n        return\n    endif\n"]
            if null:
                out += ["    if value == null then\n", "        call RemoveSavedHandle(KKLC_Data, ctx, symbol)\n", "    else\n"]
            out += [f"    call YDHashSet(KKLC_Data, {typ}, ctx, symbol, value)\n"]
            if null: out += ["    endif\n"]
            out += ["endfunction\n", f"function KKLC_SetArray_{typ} takes integer ctx, integer symbol, integer index, {base} value returns nothing\n", "    local integer page = KKLC_Array(ctx, symbol, true)\n    if page == 0 then\n        return\n    endif\n    if not KKLC_Touch(page, index) then\n        return\n    endif\n"]
            if null:
                out += ["    if value == null then\n        call RemoveSavedHandle(KKLC_Arrays, page, index)\n    else\n"]
            out += [f"    call YDHashSet(KKLC_Arrays, {typ}, page, index, value)\n"]
            if null: out += ["    endif\n"]
            out += ["endfunction\n", f"function KKLC_CopyArray_{typ} takes integer source, integer target, integer symbol returns nothing\n", "    local integer page = KKLC_Array(source, symbol, false)\n    local integer node = KKLC_IndexFirst(page)\n    local integer index\n    if source == target then\n        return\n    endif\n    call KKLC_ClearArray(target, symbol)\n    loop\n        exitwhen node == 0\n        set index = KKLC_IndexValue(node)\n", f"        call KKLC_SetArray_{typ}(target, symbol, index, YDHashGet(KKLC_Arrays, {typ}, page, index))\n", "        set node = KKLC_IndexNext(node)\n    endloop\nendfunction\n"]
        out += ["function KKLC_DestroyTimer takes timer value returns nothing\n    call KKLC_Unbind(value)\n    call DestroyTimer(value)\nendfunction\n", "function KKLC_DestroyTrigger takes trigger value returns nothing\n    call KKLC_Unbind(value)\n    call DestroyTrigger(value)\nendfunction\n"]
        return "".join(out)

    def bridge_wrapper(self, fn):
        r = fn.returns
        visibility = fn.visibility + " " if fn.visibility else ""
        out = f"\n{visibility}function {fn.source_name}_KKLCBridge takes nothing returns {r}\n    local integer kklc_frame = KKLC_BridgeAcquire()\n"
        if r != "nothing": out += f"    local {r} kklc_value\n"
        out += "    if kklc_frame == 0 then\n        return" + (" " + self.default(r) if r != "nothing" else "") + "\n    endif\n"
        out += (f"    call {fn.source_name}(kklc_frame)\n" if r == "nothing" else f"    set kklc_value = {fn.source_name}(kklc_frame)\n")
        out += "    call KKLC_Release(kklc_frame)\n"
        if r != "nothing": out += "    return kklc_value\n"
        return out + "endfunction\n"

    def run(self):
        self.parse()
        if not any(t.kind == "id" and t.value.startswith("YDLocal") for t in self.tokens):
            return self.text, {"transformed": False, "adapter_version": 1, "symbols": [], "functions": [], "captures": [], "named_pages": []}
        self.analyze()
        edits = []
        for fn in self.functions.values():
            affected = bool(fn.role) or any(c.name.startswith("YDLocal") or c.name in {"YDTriggerExecuteTrigger", "DestroyTimer", "DestroyTrigger"} for c in fn.calls) or any(parent is fn for parent, _, _, _ in self.registrations)
            if not affected:
                continue
            code = self.function_output(fn)
            if fn.name in self.bridge_helpers:
                code += self.bridge_wrapper(fn)
            edits.append((fn.start, fn.finish, code))
        output = self.text
        for a,b,code in sorted(edits, reverse=True):
            output = output[:a] + code + output[b:]
        prefix = self.wrappers()
        output = prefix + output
        if any(t.kind == "id" and (t.value.startswith("YDLocal") or t.value == "YDLOC") for t in lex(output)):
            self.fail("Incomplete rewrite left old local access behind")
        return output, {"transformed": True, "adapter_version": 1, "symbols": [{"id": i, "type": t, "name": n, "array": a} for (t,n,a),i in self.symbols.items()], "functions": [{"name": f.name, "role": f.role} for f in self.functions.values() if f.role], "captures": self.captures, "bridge_helpers": sorted(self.bridge_helpers), "named_pages": [{"id": i, "name": name} for name, i in self.named_pages.items()], "generated_prefix_length": len(prefix)}


def transform(text: str) -> tuple[str, dict]:
    """Transform a pre-Wave generated script; raise TransformError on ambiguity."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    return Adapter(text).run()


def validate_generated_callbacks(text: str) -> dict:
    """Recheck actual post-template callbacks without transforming them again.

    Includes/templates may add conditions, actions or waiting helpers after the
    adapter ran. Recognize generated dynamic entries and synchronous bridges,
    then check the final owner/alias and synchronous waiting call graphs.
    """
    adapter = Adapter(text)
    adapter.parse(trusted_output=True)
    dynamic = set()
    bridges = set()
    for fn in adapter.functions.values():
        if fn.name.endswith("_KKLCBridge") or any(c.name == "KKLC_BridgeAcquire" for c in fn.calls):
            bridges.add(fn.name)
        if any(c.name == "KKLC_AcquireOwner" and len(c.args) == 1
               and adapter.compact(adapter.code(c.args[0])) == "GetTriggeringTrigger()"
               for c in fn.calls):
            fn.role = "dynamic"
            dynamic.add(fn.name)
    if not dynamic and not bridges:
        return {"dynamic_callbacks": 0, "dynamic_registrations": 0}
    for fn in adapter.functions.values():
        for call in fn.calls:
            if call.name == "TriggerAddCondition" and len(call.args) == 2:
                for name in adapter.refs(call):
                    if name in dynamic:
                        adapter.registrations.append((fn, call, name, adapter.code(call.args[0])))
    registered = {registration[2] for registration in adapter.registrations}
    for name in dynamic - registered:
        adapter.fail("Final dynamic callback has no proven registration: " + name, adapter.functions[name].start)
    adapter.check_dynamic_conditions()
    can_wait = {fn.name for fn in adapter.functions.values()
                if any(c.name in {"TriggerSleepAction", "PolledWait", "TriggerWaitForSound", "WaitForSoundBJ"} for c in fn.calls)}
    changed = True
    while changed:
        changed = False
        for fn in adapter.functions.values():
            if fn.name not in can_wait and any(name in can_wait for c in fn.calls
                                              for name in ([c.name] + (adapter.refs(c) if c.name in SYNC_CALLBACKS else []))):
                can_wait.add(fn.name)
                changed = True
    for name in dynamic & can_wait:
        adapter.fail("Wait in final dynamic Condition callback: " + name, adapter.functions[name].start)
    for name in bridges & can_wait:
        adapter.fail("Wait in final synchronous callback bridge: " + name, adapter.functions[name].start)
    return {"dynamic_callbacks": len(dynamic), "dynamic_registrations": len(adapter.registrations)}
