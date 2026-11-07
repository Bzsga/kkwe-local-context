import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from adapter import Adapter, transform, TransformError, lex, validate_generated_callbacks


def root(body, name="Test_Actions"):
    lines = body.splitlines()
    locals_ = []
    while lines and lines[0].strip().startswith("local "):
        locals_.append(lines.pop(0))
    declarations = "\n".join(locals_) + ("\n" if locals_ else "")
    actions = "\n".join(lines)
    return f"function {name} takes nothing returns nothing\n{declarations}    YDLocalInitialize()\n{actions}\n    call YDLocal1Release()\nendfunction\n"


class AdapterTests(unittest.TestCase):
    def execute_generated(self, source, configure=None):
        """Execute adapter output and actual runtime with explicit native mocks."""
        from jass_runtime import Handle, JassRuntime, translate_jass
        output, metadata = transform(source)
        runtime = JassRuntime()
        runtime.ns.update({
            "GetTriggeringTrigger": lambda: Handle(990),
            "DoNothing": lambda: None,
            "integer": "Integer",
            "YDHashSet": lambda table, kind, parent, child, value: runtime.ns["Save" + kind](table, parent, child, value),
            "YDHashGet": lambda table, kind, parent, child: runtime.ns["Load" + kind](table, parent, child),
        })
        if configure:
            configure(runtime)
        tokens = lex(output)
        for i in range(len(tokens) - 2, -1, -1):
            if tokens[i].value == "function" and tokens[i+2].value != "takes":
                output = output[:tokens[i].start] + output[tokens[i].end:]
        code = translate_jass("globals\n    integer test_adapter_global = 0\nendglobals\n" + output)
        exec(compile(code, "<adapter-runtime-regression>", "exec"), runtime.ns)
        return runtime, metadata

    def test_scalar_wait_and_null(self):
        src = root('    call YDLocal1Set(unit, "u", GetTriggerUnit())\n    call TriggerSleepAction(0.1)\n    call YDLocalReset()\n    call YDLocal1Set(unit, "u", null)\n    call KillUnit(YDLocal1Get(unit, "u"))')
        out, meta = transform(src)
        self.assertIn("local integer kklc_ctx = KKLC_EnterRoot", out)
        self.assertIn("KKLC_Set_unit(kklc_ctx, 1, null)", out)
        self.assertIn("RemoveSavedHandle(KKLC_Data", out)
        self.assertIn("TriggerSleepAction(0.1)", out)
        self.assertEqual(meta["symbols"], [{"id": 1, "type": "unit", "name": "u", "array": False}])

    def test_array_is_separate_symbol_and_exact_index(self):
        out, meta = transform(root('    call YDLocal1Set(integer, "a", 10)\n    call YDLocal1ArraySet(integer, "a", -2147483648, 20)\n    call YDLocal1ArraySet(integer, "a1", 0, 30)'))
        self.assertEqual([x["id"] for x in meta["symbols"]], [1,2,3])
        self.assertIn("KKLC_SetArray_integer(kklc_ctx, 2, -2147483648, 20)", out)
        self.assertNotIn("StringHash", out)

    def test_strings_comments_and_nested_arguments(self):
        src = root('    // YDLocalBanana()\n    call BJDebugMsg("YDLocal1Get(integer, \\\"x\\\")")\n    call YDLocal1Set(real, "x", Pow(2., I2R(GetRandomInt(1, 4))))')
        out, _ = transform(src)
        self.assertIn('// YDLocalBanana()', out)
        self.assertIn('"YDLocal1Get(integer, \\\"x\\\")"', out)
        self.assertIn('Pow(2., I2R(GetRandomInt(1, 4)))', out)

    def test_helper_explicit_context_and_return_evaluation(self):
        src = 'function Test_Cond takes nothing returns boolean\n    return YDLocal2Get(integer, "x") > 0\nendfunction\n' + root('    call YDLocal1Set(integer, "x", 2)\n    if Test_Cond() then\n        return\n    endif')
        out, _ = transform(src)
        self.assertIn("Test_Cond takes integer kklc_parent returns boolean", out)
        self.assertIn("if Test_Cond(kklc_ctx) then", out)
        self.assertIn("set kklc_result = YDHashGet", out)
        self.assertIn("return kklc_result", out)

    def test_group_bridge_only_during_native(self):
        src = 'function Test_Group takes nothing returns nothing\n    call KillUnit(YDLocal2Get(unit, "u"))\nendfunction\n' + root('    call YDLocal1Set(unit, "u", GetTriggerUnit())\n    call ForGroup(GetUnitsInRectAll(bj_mapInitialPlayableArea), function Test_Group)\n    call TriggerSleepAction(1.)')
        out, meta = transform(src)
        self.assertIn("function Test_Group_KKLCBridge", out)
        self.assertIn("function Test_Group_KKLCBridge)", out)
        self.assertLess(out.index("call KKLC_BridgePop(kklc_bridge)"), out.index("call TriggerSleepAction"))
        self.assertEqual(meta["bridge_helpers"], ["Test_Group"])

    def test_nested_enumeration_wrapper_inherits_context_without_direct_macros(self):
        observed = []
        callbacks = ('function Inner takes nothing returns nothing\n    call YDLocal2Set(integer, "x", YDLocal2Get(integer, "x") + 1)\nendfunction\n'
                     'function Outer takes nothing returns nothing\n    call ForGroup(test_group, function Inner)\nendfunction\n')
        source = callbacks + root('    call YDLocal1Set(integer, "x", 77)\n    call ForGroup(test_group, function Outer)\n    call Observe(YDLocal1Get(integer, "x"))')
        def configure(runtime):
            runtime.ns.update({'test_group': object(), 'ForGroup': lambda group, callback: callback(), 'Observe': observed.append})
        runtime, metadata = self.execute_generated(source, configure)
        runtime.ns['Test_Actions']()
        self.assertEqual(observed, [78])
        self.assertEqual(metadata['bridge_helpers'], ['Inner', 'Outer'])
        self.assertEqual(runtime.KKLC_BridgeHead, 0)
        self.assertFalse(runtime.messages)

    def test_timer_capture_array_and_release_on_destroy(self):
        cb = 'function Test_Timer takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocalArrayGet(GetExpiredTimer(), integer, "a", 100)))\n    call DestroyTimer(GetExpiredTimer())\nendfunction\n'
        src = cb + root('    local timer ydl_timer\n    call YDLocal1ArraySet(integer, "a", 100, 7)\n    set ydl_timer = CreateTimer()\n    call TimerStart(ydl_timer, .1, false, function Test_Timer)')
        out, meta = transform(src)
        self.assertIn("KKLC_AcquireOwner(GetExpiredTimer())", out)
        self.assertIn("KKLC_CopyArray_integer(kklc_ctx, KKLC_Owner(ydl_timer), 1)", out)
        self.assertIn("KKLC_DestroyTimer(GetExpiredTimer())", out)
        self.assertEqual(len(meta["captures"]), 1)

    def test_parameter_prepare_dispatch(self):
        src = root('    local trigger ydl_trigger\n    set ydl_trigger = gg_trg_Child\n    YDLocalExecuteTrigger(ydl_trigger)\n    call YDLocal5ArraySet(integer, "a", 1, 7)\n    call YDTriggerExecuteTrigger(ydl_trigger, true)')
        out, _ = transform(src)
        self.assertIn("local integer ydl_triggerstep = 0", out)
        self.assertIn("set ydl_triggerstep = KKLC_Prepare(ydl_trigger)", out)
        self.assertIn("KKLC_Execute(ydl_trigger, true, ydl_triggerstep)", out)
        self.assertIn("set ydl_triggerstep = 0", out)

    def test_dynamic_registration_is_marked(self):
        src = 'function Test_Dynamic takes nothing returns boolean\n    call YDLocal4Set(integer, "x", YDLocal4Get(integer, "x") + 1)\n    return false\nendfunction\n' + root('    local trigger ydl_trigger\n    set ydl_trigger = CreateTrigger()\n    call YDLocalSet(ydl_trigger, integer, "x", 0)\n    call TriggerAddCondition(ydl_trigger, Condition(function Test_Dynamic))')
        out, _ = transform(src)
        self.assertIn("KKLC_MarkDynamic(ydl_trigger)", out)
        self.assertIn("KKLC_AcquireOwner(GetTriggeringTrigger())", out)

    def test_rejections(self):
        for src in [root('    call YDLocal7Set(integer, "x", 1)'), root('    call SaveInteger(YDLOC, 1, 2, 3)'), root('    call YDLocal1Set(integer, VariableName(), 1)'), 'function T takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocal3Get(integer, "x")))\nendfunction']:
            with self.subTest(src=src):
                with self.assertRaises(TransformError): transform(src)

    def test_plain_input_is_identical(self):
        src = 'function Foo takes nothing returns nothing\n    call BJDebugMsg("YDLocal1Set")\nendfunction\n'
        out, meta = transform(src)
        self.assertEqual(src, out)
        self.assertFalse(meta["transformed"])

    def test_vjass_private_and_public_functions_have_distinct_namespaces(self):
        src = ('library First initializer Init\npublic function Init takes nothing returns nothing\nendfunction\nendlibrary\n'
               'library Second initializer Init\nprivate function Init takes nothing returns nothing\nendfunction\nendlibrary\n')
        out, meta = transform(src)
        self.assertEqual(out, src)
        self.assertFalse(meta['transformed'])
        parsed = Adapter(src)
        parsed.parse()
        self.assertEqual(set(parsed.functions), {'First::Init', 'Second::Init'})
        self.assertEqual(parsed.resolve('First_Init', len(src)), 'First::Init')
        self.assertEqual(parsed.resolve('Second_Init', len(src)), 'Second_Init')

    def test_vjass_helpers_and_same_named_bridges_keep_source_spelling(self):
        def library(name):
            return ('library ' + name + '\n'
                    'private function Read takes nothing returns nothing\n'
                    '    call Observe(YDLocal2Get(integer, "x"))\nendfunction\n'
                    'private ' + root('    call YDLocal1Set(integer, "x", 7)\n    call ForGroup(test_group, function Read)', 'Init') +
                    'endlibrary\n')
        out, meta = transform(library('First') + library('Second'))
        self.assertEqual(meta['bridge_helpers'], ['First::Read', 'Second::Read'])
        self.assertEqual(out.count('private function Read_KKLCBridge takes nothing'), 2)
        self.assertEqual(out.count('function Read_KKLCBridge)'), 2)
        self.assertEqual(out.count('call Read(kklc_frame)'), 2)
        self.assertNotIn('::', out)
        self.assertEqual(validate_generated_callbacks(out), {'dynamic_callbacks': 0, 'dynamic_registrations': 0})

    def test_vjass_public_alias_call_and_nested_parent_lookup(self):
        src = ('library First\n'
               'public function Read takes nothing returns boolean\n'
               '    return YDLocal2Get(integer, "x") > 0\nendfunction\n'
               'scope Inner\nprivate ' + root('    call YDLocal1Set(integer, "x", 2)\n    if Read() then\n        call DoNothing()\n    endif', 'Init') +
               'endscope\nendlibrary\n' +
               root('    call YDLocal1Set(integer, "x", 3)\n    if First_Read() then\n        call DoNothing()\n    endif'))
        out, meta = transform(src)
        self.assertIn('if Read(kklc_ctx) then', out)
        self.assertIn('if First_Read(kklc_ctx) then', out)
        self.assertEqual({fn['name']: fn['role'] for fn in meta['functions']},
                         {'First::Read': 'helper', 'First::Inner::Init': 'root', 'Test_Actions': 'root'})
        self.assertNotIn('::', out)

    def test_vjass_execute_func_only_rejects_the_resolved_helper(self):
        helper = ('library First\nprivate function Read takes nothing returns nothing\n'
                  '    call Observe(YDLocal2Get(integer, "x"))\nendfunction\nprivate ' +
                  root('    call YDLocal1Set(integer, "x", 1)\n    call Read()', 'Init') + 'endlibrary\n')
        unrelated = ('library Second\nprivate function Read takes nothing returns nothing\nendfunction\n'
                     'private function Init takes nothing returns nothing\n    call ExecuteFunc("Read")\nendfunction\nendlibrary\n')
        transform(helper + unrelated)
        with self.assertRaisesRegex(TransformError, 'ExecuteFunc cannot supply'):
            transform(helper.replace('    call Read()', '    call Read()\n    call ExecuteFunc("Read")') + unrelated)

    def test_vjass_duplicate_globals_and_same_scope_still_rejected(self):
        fn = 'function Init takes nothing returns nothing\nendfunction\n'
        for src in [fn + fn,
                    'library First\nprivate ' + fn + 'public ' + fn + 'endlibrary\n',
                    'library First\npublic ' + fn + 'endlibrary\n' + fn.replace('Init', 'First_Init'),
                    'library First\n' + fn + 'endlibrary\nlibrary Second\n' + fn + 'endlibrary\n']:
            with self.subTest(src=src):
                with self.assertRaisesRegex(TransformError, 'Duplicate .*function'):
                    transform(src)

    def test_zinc_library_blocks_do_not_leak_into_vjass_namespaces(self):
        zinc = ('//! zinc\n#ifdef OPEN\nlibrary Foreign requires Native\n#else\nlibrary Foreign requires Other\n#endif\n'
                '{ public function Init() {} }\n//! endzinc\n')
        plain = 'library Real\nprivate function Init takes nothing returns nothing\nendfunction\nendlibrary\n'
        parsed = Adapter(zinc + plain)
        parsed.parse()
        self.assertEqual(set(parsed.functions), {'Real::Init'})
        out, meta = transform(zinc + plain)
        self.assertEqual(out, zinc + plain)
        self.assertFalse(meta['transformed'])

    def test_scoped_callback_wait_analysis_and_final_registration(self):
        first = ('library First\nprivate function Callback takes nothing returns boolean\n'
                 '    call YDLocal4Set(integer, "x", 1)\n    return false\nendfunction\nprivate ' +
                 root('    local trigger owner\n    set owner = CreateTrigger()\n    call TriggerAddCondition(owner, Condition(function Callback))', 'Init') +
                 'endlibrary\n')
        second = ('library Second\nprivate function Callback takes nothing returns boolean\n'
                  '    call TriggerSleepAction(1.)\n    return false\nendfunction\nendlibrary\n')
        out, _ = transform(first + second)
        self.assertEqual(validate_generated_callbacks(out), {'dynamic_callbacks': 1, 'dynamic_registrations': 1})
        with self.assertRaisesRegex(TransformError, 'Wait in dynamic Condition callback'):
            transform(first.replace('    call YDLocal4Set', '    call TriggerSleepAction(1.)\n    call YDLocal4Set') + second)

    def test_named_pages_are_shared_across_functions_without_private_frames(self):
        src = ('function Save takes nothing returns nothing\n'
               '    call YDLocal6Set("Shared", integer, "x", 7)\n'
               '    call YDLocal6ArraySet("Shared", integer, "x", -2147483648, 8)\nendfunction\n'
               'function Read takes nothing returns nothing\n'
               '    call Observe(YDLocal6Get("Shared", itemcode, "x"))\n'
               '    call Observe(YDLocal6ArrayGet("Other", integer, "x", 2147483647))\n'
               '    call YDLocal6Release("Shared")\nendfunction\n')
        out, meta = transform(src)
        self.assertEqual(meta['named_pages'], [{'id': 1, 'name': 'Shared'}, {'id': 2, 'name': 'Other'}])
        self.assertEqual(meta['functions'], [])
        self.assertIn('KKLC_Set_integer(KKLC_NamedOwner(1), 1, 7)', out)
        self.assertIn('KKLC_SetArray_integer(KKLC_NamedOwner(1), 2, -2147483648, 8)', out)
        self.assertIn('YDHashGet(KKLC_Data, integer, KKLC_NamedOwner(1), 1)', out)
        self.assertIn('KKLC_Array(KKLC_NamedOwner(2), 2, false)', out)
        self.assertIn('call KKLC_ClearContext(KKLC_NamedOwner(1))', out)
        self.assertNotIn('local integer kklc_ctx', out)
        self.assertNotIn('YDLocal6', out)

    def test_named_page_names_require_literals_and_release_validates_arity(self):
        for statement in ['call YDLocal6Set(PageName(), integer, "x", 1)',
                          'call YDLocal6Get("escaped\\n", integer, "x")',
                          'call YDLocal6Release(PageName())',
                          'call YDLocal6Release()',
                          'call YDLocal6Release("Page", "Extra")']:
            with self.subTest(statement=statement):
                with self.assertRaises(TransformError):
                    transform('function Test takes nothing returns nothing\n    ' + statement + '\nendfunction\n')

    def test_parameter_only_callback_uses_registered_owner_and_keeps_child_frame(self):
        callback = ('function Callback takes nothing returns nothing\n'
                    '    local trigger child = gg_trg_Child\n'
                    '    YDLocalExecuteTrigger(child)\n'
                    '    call YDLocal5Set(integer, "x", 1)\n'
                    '    call YDTriggerExecuteTrigger(child, false)\n'
                    '    call YDLocal4Release()\n'
                    '    call DestroyTrigger(GetTriggeringTrigger())\nendfunction\n')
        src = callback + ('function Register takes nothing returns nothing\n'
                          '    local trigger owner = CreateTrigger()\n'
                          '    call TriggerAddCondition(owner, Condition(function Callback))\nendfunction\n')
        out, meta = transform(src)
        self.assertEqual(meta['functions'], [{'name': 'Callback', 'role': 'dynamic'}])
        self.assertIn('local integer kklc_ctx = KKLC_AcquireOwner(GetTriggeringTrigger())', out)
        self.assertIn('set ydl_triggerstep = KKLC_Prepare(child)', out)
        self.assertIn('KKLC_Set_integer(ydl_triggerstep, 1, 1)', out)
        self.assertIn('KKLC_Execute(child, false, ydl_triggerstep)', out)
        self.assertIn('set ydl_triggerstep = 0', out)
        self.assertEqual(validate_generated_callbacks(out), {'dynamic_callbacks': 1, 'dynamic_registrations': 1})

    def test_preprocessor_branches_are_distinct_from_nested_jass_control_flow(self):
        src = root('''    local trigger child = gg_trg_Child
    #ifdef FEATURE
        if true then
#define YDTRIGGER_COMMON_LOOP(n) call DoNothing()
#include <YDTrigger/Common/loop.h>
            call DoNothing()
        endif
    #else
        call DoNothing()
    #endif
    YDLocalExecuteTrigger(child)
    call YDLocal5Set(integer, "x", 1)
    call YDTriggerExecuteTrigger(child, true)''')
        out, _ = transform(src)
        self.assertIn('#ifdef FEATURE', out)
        with self.assertRaisesRegex(TransformError, 'cross a conditional'):
            transform(root('''    local trigger child = gg_trg_Child
    #ifdef FEATURE
        YDLocalExecuteTrigger(child)
    #endif
    call YDTriggerExecuteTrigger(child, true)'''))

    def test_dynamic_owner_can_come_from_proven_local_trigger_storage(self):
        callback = ('function Callback takes nothing returns boolean\n'
                    '    call YDLocal4Set(integer, "x", 1)\n    return false\nendfunction\n')
        body = '''    local trigger owner
    call YDLocal1Set(trigger, "saved", CreateTrigger())
    set owner = YDLocal1Get(trigger, "saved")
    call TriggerAddCondition(owner, Condition(function Callback))'''
        out, _ = transform(callback + root(body))
        self.assertIn('call KKLC_MarkDynamic(owner)', out)
        conditional = body.replace('    call YDLocal1Set(trigger, "saved", CreateTrigger())',
                                   '    if true then\n        call YDLocal1Set(trigger, "saved", CreateTrigger())\n    endif')
        with self.assertRaisesRegex(TransformError, 'opaque expression'):
            transform(callback + root(conditional))
        overwritten = body.replace('    set owner =',
                                   '    call YDLocal1Set(trigger, "saved", UnknownTriggerGetter())\n    set owner =')
        with self.assertRaisesRegex(TransformError, 'opaque expression'):
            transform(callback + root(overwritten))

    def test_owner_context_reaches_callback_with_only_synchronous_enumeration(self):
        for owner, role, registration in [
                ('GetExpiredTimer()', 'timer', 'call TimerStart(owner, 1., true, function Callback)'),
                ('GetTriggeringTrigger()', 'dynamic', 'call TriggerAddCondition(owner, Condition(function Callback))')]:
            with self.subTest(role=role):
                returns = 'nothing' if role == 'timer' else 'boolean'
                tail = '' if role == 'timer' else '    return false\n'
                source = ('function Enumerated takes nothing returns nothing\n'
                          '    call Observe(YDLocalGet(' + owner + ', integer, "x"))\nendfunction\n'
                          'function Callback takes nothing returns ' + returns + '\n'
                          '    call ForForce(players, function Enumerated)\n' + tail + 'endfunction\n'
                          'function Register takes nothing returns nothing\n'
                          '    local ' + ('timer' if role == 'timer' else 'trigger') + ' owner = ' +
                          ('CreateTimer()' if role == 'timer' else 'CreateTrigger()') + '\n'
                          '    ' + registration + '\nendfunction\n')
                out, meta = transform(source)
                self.assertEqual({fn['name']: fn['role'] for fn in meta['functions']},
                                 {'Enumerated': 'helper', 'Callback': role})
                self.assertIn('KKLC_AcquireOwner(' + owner + ')', out)
                self.assertIn('function Enumerated_KKLCBridge)', out)
                validate_generated_callbacks(out)

    def test_item_and_destructable_enumeration_use_synchronous_context_bridges(self):
        callback = ('function Enumerated takes nothing returns nothing\n'
                    '    call Observe(YDLocal2Get(integer, "x"))\nendfunction\n')
        for native in ['EnumItemsInRect', 'EnumItemsInRectBJ', 'EnumDestructablesInRect',
                       'EnumDestructablesInRectAll', 'EnumDestructablesInCircleBJ']:
            with self.subTest(native=native):
                args = ('area, null, function Enumerated' if native in {'EnumItemsInRect', 'EnumDestructablesInRect'} else
                        '10., center, function Enumerated' if native == 'EnumDestructablesInCircleBJ' else
                        'area, function Enumerated')
                out, meta = transform(callback + root('    call YDLocal1Set(integer, "x", 7)\n    call ' + native + '(' + args + ')'))
                self.assertEqual(meta['bridge_helpers'], ['Enumerated'])
                self.assertIn('function Enumerated_KKLCBridge)', out)
                self.assertIn('KKLC_BridgePop(kklc_bridge)', out)

    def test_nested_synchronous_filter_reference_is_checked_at_actual_reference(self):
        callback = ('function Matches takes nothing returns boolean\n'
                    '    return YDLocal2Get(integer, "x") > 0\nendfunction\n')
        source = callback + root('    call YDLocal1Set(integer, "x", 7)\n'
                                 '    call YDLocal1Set(group, "g", GetUnitsInRectMatching(area, Condition(function Matches)))')
        out, meta = transform(source)
        self.assertEqual(meta['bridge_helpers'], ['Matches'])
        self.assertIn('Condition(function Matches_KKLCBridge)', out)
        self.assertIn('KKLC_BridgePop(kklc_bridge)', out)
        with self.assertRaisesRegex(TransformError, 'Unrecognized or asynchronous'):
            transform(source.replace('Condition(function Matches)', 'UnknownAsync(function Matches)'))

    def test_preprocessor_macro_headers_preserve_declaration_and_parameter_spacing(self):
        headers = ['#define YDTRIGGER_COMMON_LOOP_LIMITS (0, 15)',
                   '#define FUNCTION_LIKE(n, value) call Observe(n, value)',
                   '#define OBJECT_LIKE (Observe(1))']
        body = '\n'.join(headers) + '\n#define WRITE_VALUE(n) call YDLocal1Set(integer, "x", (n))\n'
        out, _ = transform(root(body))
        for header in headers:
            self.assertIn(header, out)
        self.assertIn('#define WRITE_VALUE(n) call KKLC_Set_integer(kklc_ctx, 1, (n))', out)
        parsed = Adapter(root(body))
        parsed.parse()
        self.assertFalse(any(call.source_name in {'YDTRIGGER_COMMON_LOOP_LIMITS', 'FUNCTION_LIKE', 'OBJECT_LIKE', 'WRITE_VALUE'}
                             for call in parsed.call_map.values()))

    def test_call_indexes_keep_recursion_and_synchronous_references_distinct(self):
        src = ('function Helper takes nothing returns nothing\n    call Helper()\nendfunction\n'
               'function Caller takes nothing returns nothing\n'
               '    call ForGroup(group, function Helper)\n'
               '    call UnknownAsync(function Caller)\nendfunction\n')
        parsed = Adapter(src)
        parsed.parse()
        self.assertEqual(parsed.synchronous_callers['Helper'], {'Helper', 'Caller'})
        self.assertNotIn('Caller', parsed.synchronous_callers)
        self.assertEqual(parsed.all_callers['Caller'], {'Caller'})
        for fn in parsed.functions.values():
            self.assertEqual(fn.call_starts, sorted(call.start for call in fn.calls))

    def test_final_dynamic_owner_tracks_generated_trigger_set_and_native_load(self):
        callback = ('function Callback takes nothing returns boolean\n'
                    '    local integer kklc_ctx = KKLC_AcquireOwner(GetTriggeringTrigger())\n'
                    '    call KKLC_Release(kklc_ctx)\n    return false\nendfunction\n')
        body = ('    local integer kklc_ctx = KKLC_EnterRoot(GetTriggeringTrigger())\n'
                '    local trigger owner\n'
                '    call KKLC_Set_trigger(kklc_ctx, 17, CreateTrigger())\n'
                '    set owner = LoadTriggerHandle(KKLC_Data, kklc_ctx, 17)\n'
                '    call TriggerAddCondition(owner, Condition(function Callback))\n')
        def program(replacement):
            return callback + 'function Register takes nothing returns nothing\n' + replacement + 'endfunction\n'
        self.assertEqual(validate_generated_callbacks(program(body)), {'dynamic_callbacks': 1, 'dynamic_registrations': 1})
        invalid = [
            body.replace('    call KKLC_Set_trigger(kklc_ctx, 17, CreateTrigger())',
                         '    if true then\n        call KKLC_Set_trigger(kklc_ctx, 17, CreateTrigger())\n    endif'),
            body.replace('    set owner =', '    call KKLC_Set_trigger(kklc_ctx, 17, UnknownTriggerGetter())\n    set owner ='),
            body.replace('KKLC_Data, kklc_ctx, 17', 'KKLC_Data, kklc_ctx, 18'),
            body.replace('KKLC_Data, kklc_ctx, 17', 'OtherData, kklc_ctx, 17'),
            body.replace('KKLC_Data, kklc_ctx, 17', 'KKLC_Data, other_context, 17'),
        ]
        for replacement in invalid:
            with self.subTest(replacement=replacement):
                with self.assertRaisesRegex(TransformError, 'opaque expression'):
                    validate_generated_callbacks(program(replacement))

    def test_aliases_share_native_storage_bucket(self):
        out, meta = transform(root('    call YDLocal1Set(unitcode, "id", 42)\n    call BJDebugMsg(I2S(YDLocal1Get(integer, "id")))\n    call YDLocal1Set(degree, "angle", 90.)\n    call BJDebugMsg(R2S(YDLocal1Get(radian, "angle")))'))
        self.assertEqual(len(meta["symbols"]), 2)
        self.assertIn("KKLC_Set_integer(kklc_ctx, 1, 42)", out)
        self.assertIn("YDHashGet(KKLC_Data, integer, kklc_ctx, 1)", out)
        self.assertIn("KKLC_Set_real(kklc_ctx, 2, 90.)", out)
        self.assertIn("YDHashGet(KKLC_Data, real, kklc_ctx, 2)", out)

    def test_prepare_cross_branch_rejected_and_existing_local_initialized(self):
        with self.assertRaisesRegex(TransformError, "cross a conditional"):
            transform(root('    if true then\n        YDLocalExecuteTrigger(gg_trg_A)\n    endif\n    call YDTriggerExecuteTrigger(gg_trg_A, true)'))
        out, _ = transform(root('    local integer ydl_triggerstep\n    YDLocalExecuteTrigger(gg_trg_A)\n    call YDTriggerExecuteTrigger(gg_trg_A, true)'))
        self.assertIn("local integer ydl_triggerstep = 0", out)

    def test_reassigned_owner_captures_parent_again(self):
        cb = 'function TimerCB takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocalGet(GetExpiredTimer(), integer, "x")))\nendfunction\n'
        src = cb + root('    local timer ydl_timer\n    call YDLocal1Set(integer, "x", 77)\n    set ydl_timer = CreateTimer()\n    call YDLocalSet(ydl_timer, integer, "x", 11)\n    call TimerStart(ydl_timer, .1, false, function TimerCB)\n    call DestroyTimer(ydl_timer)\n    set ydl_timer = CreateTimer()\n    call TimerStart(ydl_timer, .1, false, function TimerCB)')
        out, meta = transform(src)
        self.assertEqual(len(meta["captures"]), 1)
        self.assertIn("KKLC_Set_integer(KKLC_Owner(ydl_timer), 1, YDHashGet(KKLC_Data, integer, kklc_ctx, 1))", out)

    def test_explicit_array_element_does_not_hide_other_capture_elements(self):
        cb = 'function TimerCB takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocalArrayGet(GetExpiredTimer(), integer, "a", 1)))\nendfunction\n'
        src = cb + root('    local timer ydl_timer\n    call YDLocal1ArraySet(integer, "a", 1, 77)\n    set ydl_timer = CreateTimer()\n    call YDLocalArraySet(ydl_timer, integer, "a", 0, 11)\n    call TimerStart(ydl_timer, .1, false, function TimerCB)')
        out, meta = transform(src)
        parent = out[out.index("function Test_Actions"):]
        self.assertLess(parent.index("call KKLC_CopyArray_integer"), parent.index("call KKLC_SetArray_integer(KKLC_Owner"))
        self.assertEqual(len(meta["captures"]), 1)

    def test_wait_in_group_bridge_and_nested_pending_rejected(self):
        cb = 'function GroupCB takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocal2Get(integer, "x")))\n    call TriggerSleepAction(1.)\nendfunction\n'
        with self.assertRaisesRegex(TransformError, "Wait in synchronous"):
            transform(cb + root('    call ForGroup(bj_lastCreatedGroup, function GroupCB)'))
        with self.assertRaisesRegex(TransformError, "Nested parameter"):
            transform(root('    YDLocalExecuteTrigger(gg_trg_A)\n    YDLocalExecuteTrigger(gg_trg_B)\n    call YDTriggerExecuteTrigger(gg_trg_B, false)\n    call YDTriggerExecuteTrigger(gg_trg_A, false)'))

    def test_destructor_in_ordinary_helper_is_intercepted(self):
        src = 'function DestroyIt takes timer t returns nothing\n    call DestroyTimer(t)\nendfunction\n' + root('    call YDLocal1Set(integer, "x", 1)')
        out, _ = transform(src)
        self.assertIn("call KKLC_DestroyTimer(t)", out)

    def test_macro_outside_function_and_raw_code_ref_are_rejected(self):
        with self.assertRaises(TransformError):
            transform('call YDLocal1Set(integer, "x", 1)\n')
        cb = 'function GroupCB takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocal2Get(integer, "x")))\nendfunction\n'
        with self.assertRaisesRegex(TransformError, "Unrecognized or asynchronous"):
            transform(cb + root('    call TimerStart(CreateTimer(), .1, false, function GroupCB)'))

    def test_mid_body_clear_is_not_silently_ignored(self):
        with self.assertRaisesRegex(TransformError, "Mid-body"):
            transform(root('    call YDLocal1Set(integer, "x", 77)\n    call YDLocal1Release()\n    call BJDebugMsg(I2S(YDLocal1Get(integer, "x")))'))

    def test_nested_timer_captures_its_parent_timer_frame(self):
        inner = 'function Inner takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocalGet(GetExpiredTimer(), integer, "x")))\nendfunction\n'
        outer = 'function Outer takes nothing returns nothing\n    local timer t\n    call YDLocalSet(GetExpiredTimer(), integer, "x", 77)\n    set t = CreateTimer()\n    call TimerStart(t, .1, false, function Inner)\nendfunction\n'
        src = inner + outer + root('    local timer t\n    set t = CreateTimer()\n    call TimerStart(t, .1, false, function Outer)')
        out, meta = transform(src)
        self.assertEqual([c["parent"] for c in meta["captures"]], ["Outer"])
        self.assertIn("KKLC_Set_integer(KKLC_Owner(t), 1, YDHashGet(KKLC_Data, integer, kklc_ctx, 1))", out)

    def test_local_initialized_owner_capture_keeps_all_declarations_first(self):
        cb = 'function TimerCB takes nothing returns nothing\n    call BJDebugMsg(I2S(YDLocalArrayGet(GetExpiredTimer(), integer, "a", 1)))\nendfunction\n'
        src = cb + root('    local timer t = CreateTimer()\n    local integer n = 3\n    call YDLocal1ArraySet(integer, "a", 1, 77)\n    call TimerStart(t, .1, false, function TimerCB)')
        out, _ = transform(src)
        parent = out[out.index("function Test_Actions"):]
        self.assertLess(parent.index("local integer n = 3"), parent.index("call KKLC_CopyArray_integer"))
        self.assertLess(parent.index("if kklc_ctx == 0"), parent.index("call KKLC_CopyArray_integer"))
        self.assertLess(parent.index("KKLC_SetArray_integer(kklc_ctx, 1, 1, 77)"), parent.index("call KKLC_CopyArray_integer"))

    def test_callback_reference_text_in_string_is_not_code(self):
        out, _ = transform(root('    call BJDebugMsg("function NotARealHelper")\n    call YDLocal1Set(integer, "x", 1)'))
        self.assertIn('"function NotARealHelper"', out)

    def test_root_as_timer_callback_and_bad_macro_arity_reject(self):
        with self.assertRaisesRegex(TransformError, "Root local"):
            transform(root('    call YDLocal1Set(integer, "x", 1)', "Callback") + root('    call TimerStart(CreateTimer(), .1, false, function Callback)'))
        with self.assertRaisesRegex(TransformError, "Unexpected arguments"):
            transform(root('    call YDLocalReset(123)'))

    def test_multiline_comments_and_argument_comment_newlines(self):
        src = 'function T takes nothing returns nothing\n    /* YDLocalFake(\n       ignored ) */\n    local integer x = 0\n    YDLocalInitialize()\n    call YDLocal1Set(integer, "x", GetRandomInt(1 // keep newline\n       , 2))\n    call YDLocal1Release()\n    /* tail\n       comment */\nendfunction\n'
        out, _ = transform(src)
        self.assertIn("/* YDLocalFake(\n       ignored ) */", out)
        self.assertIn("1 // keep newline\n", out)

    def test_reassignment_between_prepare_and_dispatch_is_rejected(self):
        with self.assertRaisesRegex(TransformError, "reassigned"):
            transform(root('    local trigger ydl_trigger\n    set ydl_trigger = gg_trg_A\n    YDLocalExecuteTrigger(ydl_trigger)\n    call YDLocal5Set(integer, "x", 77)\n    set ydl_trigger = gg_trg_B\n    call YDTriggerExecuteTrigger(ydl_trigger, false)'))

    def test_mixed_dynamic_and_ordinary_actions_rejected(self):
        cb = 'function D takes nothing returns boolean\n    call YDLocal4Set(integer, "x", 1)\n    return false\nendfunction\n'
        with self.assertRaisesRegex(TransformError, "Mixed dynamic"):
            transform(cb + root('    local trigger t\n    set t = CreateTrigger()\n    call TriggerAddCondition(t, Condition(function D))\n    call TriggerAddAction(t, function Ordinary)'))

    def test_conditional_owner_parameters_fail_before_emitting_wrong_snapshot(self):
        for array in (False, True):
            with self.subTest(array=array):
                get = 'YDLocalArrayGet(GetExpiredTimer(), integer, "x", 1)' if array else 'YDLocalGet(GetExpiredTimer(), integer, "x")'
                callback = 'function TimerCB takes nothing returns nothing\n    call Observe(' + get + ')\nendfunction\n'
                parent_set = 'call YDLocal1ArraySet(integer, "x", 1, 77)' if array else 'call YDLocal1Set(integer, "x", 77)'
                owner_set = 'call YDLocalArraySet(t, integer, "x", 0, 11)' if array else 'call YDLocalSet(t, integer, "x", 11)'
                body = '    local timer t\n    ' + parent_set + '\n    set t = CreateTimer()\n    if false then\n        ' + owner_set + '\n    endif\n    call TimerStart(t, .1, false, function TimerCB)'
                with self.assertRaisesRegex(TransformError, "capture parameters cross"):
                    transform(callback + root(body))
                # Both the explicit parameter and registration in the same
                # branch have a proven execution order and stay supported.
                body = body.replace('    endif\n    call TimerStart', '    call TimerStart') + '\n    endif'
                transform(callback + root(body))

    def test_skipped_loop_dispatch_does_not_leak_when_next_frame_is_prepared(self):
        from jass_runtime import Handle
        for finish in ('', '    return\n'):
            with self.subTest(early_return=bool(finish)):
                prepared = []
                def configure(runtime):
                    original = runtime.KKLC_Prepare
                    def prepare(target):
                        ctx = original(target)
                        prepared.append(ctx)
                        return ctx
                    runtime.ns.update({'KKLC_Prepare': prepare, 'gg_trg_A': Handle(991), 'gg_trg_B': Handle(992)})
                body = ('    loop\n        YDLocalExecuteTrigger(gg_trg_A)\n'
                        '        call YDLocal5Set(integer, "x", 77)\n        exitwhen true\n'
                        '        call YDTriggerExecuteTrigger(gg_trg_A, false)\n    endloop\n' + finish)
                if not finish:
                    body += '    YDLocalExecuteTrigger(gg_trg_B)\n    call YDTriggerExecuteTrigger(gg_trg_B, false)'
                runtime, _ = self.execute_generated(root(body), configure)
                runtime.ns['Test_Actions']()
                self.assertEqual(len(prepared), 1 if finish else 2)
                self.assertTrue(all(not runtime.KKLC_Alive(ctx) for ctx in prepared))
                self.assertFalse(runtime.messages)

    def test_parameter_frame_slot_and_access_interval_are_owned_by_adapter(self):
        pair = '    YDLocalExecuteTrigger(gg_trg_A)\n    call YDTriggerExecuteTrigger(gg_trg_A, false)'
        for body in ('    local integer ydl_triggerstep\n    set ydl_triggerstep = 5\n' + pair,
                     pair + '\n    call YDLocal5Set(integer, "x", 77)'):
            with self.subTest(body=body), self.assertRaisesRegex(TransformError, 'parameter-frame|outside preparation'):
                transform(root(body))

    def test_computed_execute_func_cannot_call_rewritten_helper_with_no_context(self):
        callback = 'function GroupCB takes nothing returns nothing\n    call YDLocal2Set(integer, "x", 77)\nendfunction\n'
        body = '    call ForGroup(bj_lastCreatedGroup, function GroupCB)\n    call ExecuteFunc("Group" + "CB")'
        with self.assertRaisesRegex(TransformError, 'Computed ExecuteFunc'):
            transform(callback + root(body))
        transform(callback + root(body.replace('"Group" + "CB"', '"Unrelated"')))

    def test_dynamic_conditions_reject_extra_alias_and_forwarded_actions(self):
        callbacks = ('function D takes nothing returns boolean\n    call YDLocal4Set(integer, "x", 77)\n    return true\nendfunction\n'
                     'function Other takes nothing returns boolean\n    return false\nendfunction\n'
                     'function AddIt takes trigger arg returns nothing\n    call TriggerAddAction(arg, function Ordinary)\nendfunction\n'
                     'function Forward takes trigger target returns nothing\n    call AddIt(target)\nendfunction\n')
        setup = '    local trigger t\n    local trigger alias\n    set t = CreateTrigger()\n    set alias = t\n'
        for extra in ('    call TriggerAddCondition(alias, Condition(function Other))\n',
                      '    call Forward(alias)\n'):
            with self.subTest(extra=extra), self.assertRaisesRegex(TransformError, 'Additional condition|Mixed dynamic'):
                transform(callbacks + root(setup + extra + '    call TriggerAddCondition(t, Condition(function D))'))
        with self.assertRaisesRegex(TransformError, 'Composite dynamic'):
            transform(callbacks + root(setup + '    call TriggerAddCondition(t, And(Condition(function Other), Condition(function D)))'))
        # A reused local that now owns a fresh ordinary trigger is distinct.
        transform(callbacks + root(setup + '    call TriggerAddCondition(t, Condition(function D))\n    set t = CreateTrigger()\n    call Forward(t)'))

    def test_final_callbacks_repeat_alias_and_wait_checks_after_includes(self):
        callbacks = ('function D takes nothing returns boolean\n    call YDLocal4Set(integer, "x", 77)\n    call LateHelper()\n    return true\nendfunction\n')
        source = callbacks + root('    local trigger t\n    set t = CreateTrigger()\n    set udg_Dynamic = t\n    call TriggerAddCondition(t, Condition(function D))')
        output, _ = transform(source)
        self.assertEqual(validate_generated_callbacks(output)['dynamic_callbacks'], 1)
        late_wait = 'function LateHelper takes nothing returns nothing\n    call TriggerSleepAction(.1)\nendfunction\n'
        with self.assertRaisesRegex(TransformError, 'Wait in final dynamic'):
            validate_generated_callbacks(output + late_wait)
        with self.assertRaisesRegex(TransformError, 'Wait in dynamic'):
            transform(source + late_wait)
        late_condition = 'function LateHelper takes nothing returns nothing\n    call TriggerAddCondition(udg_Dynamic, Condition(function Other))\nendfunction\n'
        with self.assertRaisesRegex(TransformError, 'Additional condition'):
            validate_generated_callbacks(output + late_condition)

    def test_dynamic_registration_requires_a_proven_owner_source(self):
        callback = 'function D takes nothing returns boolean\n    call YDLocal4Set(integer, "x", 77)\n    return true\nendfunction\n'
        getter = root('    local trigger t\n    set t = GetSharedTrigger()\n    call TriggerAddCondition(t, Condition(function D))')
        with self.assertRaisesRegex(TransformError, 'opaque expression'):
            transform(callback + getter)
        parameter = 'function Register takes trigger owner returns nothing\n    call TriggerAddCondition(owner, Condition(function D))\nendfunction\n'
        with self.assertRaisesRegex(TransformError, 'parameter owner'):
            transform(callback + parameter)

    def test_null_cleanup_does_not_alias_independent_global_triggers(self):
        callbacks = ('function D takes nothing returns boolean\n    call YDLocal4Set(integer, "x", 77)\n    return true\nendfunction\n'
                     'function Ordinary takes nothing returns nothing\n    call DoNothing()\nendfunction\n'
                     'function SetupNormal takes nothing returns nothing\n    set savedNormal = CreateTrigger()\n'
                     '    call TriggerAddAction(savedNormal, function Ordinary)\nendfunction\n'
                     'function Cleanup takes nothing returns nothing\n    set savedNormal = null\n    set savedDynamic = null\nendfunction\n')
        source = callbacks + root('    local trigger t\n    set t = CreateTrigger()\n    set savedDynamic = t\n    call TriggerAddCondition(t, Condition(function D))')
        output, _ = transform(source)
        self.assertEqual(validate_generated_callbacks(output)['dynamic_callbacks'], 1)
        # A real cross-function alias still combines these two owners and
        # must retain the original rejection instead of weakening that check.
        with self.assertRaisesRegex(TransformError, 'Mixed dynamic'):
            transform(source.replace('set savedNormal = CreateTrigger()', 'set savedNormal = savedDynamic'))

    def test_final_sync_bridge_rejects_late_direct_and_nested_wait_without_dynamic(self):
        callback = ('function GroupCB takes nothing returns nothing\n'
                    '    call YDLocal2Set(integer, "x", 77)\n    call LateHelper()\nendfunction\n')
        output, _ = transform(callback + root('    call ForGroup(test_group, function GroupCB)'))
        self.assertEqual(validate_generated_callbacks(output)['dynamic_callbacks'], 0)
        prefix = 'function LateHelper takes nothing returns nothing\n'
        for late in (
            prefix + '    call TriggerSleepAction(.1)\nendfunction\n',
            'function WaitHelper takes nothing returns nothing\n    call PolledWait(.1)\nendfunction\n'
            + prefix + '    call ForGroup(test_group, function WaitHelper)\nendfunction\n',
        ):
            with self.subTest(late=late), self.assertRaisesRegex(TransformError, 'Wait in final synchronous callback bridge'):
                validate_generated_callbacks(output + late)
        validate_generated_callbacks(output + prefix + '    call DoNothing()\nendfunction\n')

    def test_callback_release_clears_live_frame_and_preserves_dynamic_dispatch(self):
        from jass_runtime import Handle
        observed = []
        target = Handle(993)
        callbacks = ('function D takes nothing returns boolean\n'
                     '    call YDLocal4Set(integer, "x", 77)\n    call YDLocal4Release()\n'
                     '    call Observe(YDLocal4Get(integer, "x"))\n'
                     '    call YDLocal4Set(integer, "x", 88)\n    call YDLocalRelease(GetTriggeringTrigger())\n'
                     '    call Observe(YDLocal4Get(integer, "x"))\n    return false\nendfunction\n')
        body = ('    local trigger t\n    set t = CreateTrigger()\n    call TriggerAddCondition(t, Condition(function D))\n'
                '    YDLocalExecuteTrigger(t)\n    call YDTriggerExecuteTrigger(t, false)\n'
                '    YDLocalExecuteTrigger(t)\n    call YDTriggerExecuteTrigger(t, false)')
        def configure(runtime):
            runtime.ns.update({'CreateTrigger': lambda: target, 'Condition': lambda callback: callback,
                               'TriggerAddCondition': lambda target, callback: setattr(target, 'condition', callback),
                               'GetTriggeringTrigger': lambda: target, 'Observe': observed.append})
        runtime, _ = self.execute_generated(callbacks + root(body), configure)
        runtime.ns['Test_Actions']()
        self.assertEqual(observed, [0, 0, 0, 0])
        self.assertTrue(runtime.KKLC_IsDynamic(target))
        self.assertFalse(runtime.messages)

    def test_failed_inner_bridge_does_not_dispatch_through_outer_context(self):
        """Execute generated functions and the real runtime, with native mocks.

        Exhaust allocation only after entry and parameter frames exist. Before
        the guard this exact scenario runs the inner callback using the still
        active outer bridge and changes its local x to 99.
        """
        from jass_runtime import Handle, JassRuntime, translate_jass

        callback = ('function InnerEnum takes nothing returns nothing\n'
                    '    call YDLocal2Set(integer, "x", 99)\n'
                    'endfunction\n')
        for result_type, expected in (("nothing", None), ("boolean", False), ("integer", 0)):
            with self.subTest(result_type=result_type):
                entry = ('function InnerEntry takes nothing returns ' + result_type + '\n'
                         '    local integer ydl_triggerstep\n'
                         '    YDLocalInitialize()\n'
                         '    call YDLocal1Set(integer, "x", 22)\n'
                         '    YDLocalExecuteTrigger(gg_trg_Target)\n'
                         '    call YDLocal5Set(integer, "x", 33)\n'
                         '    call ArmExhaustion()\n'
                         '    call ForGroup(test_group, function InnerEnum)\n'
                         '    call YDTriggerExecuteTrigger(gg_trg_Target, false)\n'
                         '    call YDLocal1Release()\n')
                if result_type != "nothing":
                    entry += '    return ' + ('true' if result_type == "boolean" else '123') + '\n'
                entry += 'endfunction\n'
                transformed, metadata = transform(callback + entry)
                # The existing strict JASS translator handles function bodies;
                # lower only JASS's code-reference keyword for Python execution.
                tokens = lex(transformed)
                for i in range(len(tokens) - 2, -1, -1):
                    if tokens[i].value == "function" and tokens[i+2].value != "takes":
                        transformed = transformed[:tokens[i].start] + transformed[tokens[i].end:]
                executable = translate_jass('globals\n    integer test_translation_global = 0\nendglobals\n' + transformed)
                runtime = JassRuntime()
                native_calls = []
                acquired = []
                prepared = []
                enter_root = runtime.KKLC_EnterRoot
                prepare = runtime.KKLC_Prepare

                def record_entry(target):
                    ctx = enter_root(target)
                    acquired.append(ctx)
                    return ctx

                def record_prepare(target):
                    ctx = prepare(target)
                    prepared.append(ctx)
                    return ctx

                def exhaust():
                    self.assertEqual(runtime.KKLC_FreeHead, 0)
                    runtime.ns["KKLC_NextId"] = 2147483647

                def for_group(group, callback_fn):
                    native_calls.append(group)
                    callback_fn()

                runtime.ns.update({
                    "KKLC_EnterRoot": record_entry,
                    "KKLC_Prepare": record_prepare,
                    "ArmExhaustion": exhaust,
                    "ForGroup": for_group,
                    "GetTriggeringTrigger": lambda: Handle(901),
                    "DoNothing": lambda: None,
                    "integer": "Integer",
                    "YDHashSet": lambda table, kind, parent, child, value: runtime.ns["Save" + kind](table, parent, child, value),
                    "YDHashGet": lambda table, kind, parent, child: runtime.ns["Load" + kind](table, parent, child),
                    "gg_trg_Target": Handle(902),
                    "test_group": object(),
                })
                exec(compile(executable, "<actual-adapter-output>", "exec"), runtime.ns)
                outer_ctx = runtime.KKLC_Alloc()
                x_symbol = next(s["id"] for s in metadata["symbols"] if s["name"] == "x" and not s["array"])
                runtime.save("KKLC_Data", outer_ctx, x_symbol, 11)
                outer_bridge = runtime.KKLC_BridgePush(outer_ctx)
                self.assertNotEqual(outer_bridge, 0)

                actual = runtime.ns["InnerEntry"]()

                self.assertEqual(actual, expected)
                self.assertEqual(native_calls, [])
                self.assertEqual(runtime.load("KKLC_Data", outer_ctx, x_symbol), 11)
                self.assertEqual(runtime.KKLC_BridgeHead, outer_bridge)
                self.assertEqual(runtime.refs(outer_ctx), 2)
                self.assertEqual(len(acquired), 1)
                self.assertEqual(len(prepared), 1)
                self.assertFalse(runtime.KKLC_Alive(acquired[0]))
                self.assertFalse(runtime.KKLC_Alive(prepared[0]))
                self.assertTrue(any("exhaust" in message for message in runtime.messages))
                runtime.KKLC_BridgePop(outer_bridge)
                runtime.KKLC_Release(outer_ctx)
                self.assertEqual(runtime.KKLC_BridgeHead, 0)


if __name__ == "__main__":
    unittest.main()
