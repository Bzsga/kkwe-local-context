from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapter
from final_output import finalize


WRAPPERS = '''function KKLC_DestroyTimer takes timer value returns nothing
    call KKLC_Unbind(value)
    call DestroyTimer(value)
endfunction
function KKLC_DestroyTrigger takes trigger value returns nothing
    call KKLC_Unbind(value)
    call DestroyTrigger(value)
endfunction
'''
HELPER = '''function Shared takes integer kklc_parent returns nothing
    call DoNothing()
endfunction
'''


class FinalOutputTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        def callbacks(text):
            self.seen.append(text)
            return {'checked': True}
        self.adapter = types.SimpleNamespace(lex=adapter.lex, Adapter=adapter.Adapter,
                                             validate_generated_callbacks=callbacks)

    def test_private_initializers_in_distinct_libraries_remain_valid(self):
        text = WRAPPERS + '''library First initializer Init
private function Init takes nothing returns nothing
endfunction
endlibrary
library Second initializer Init
private function Init takes nothing returns nothing
endfunction
endlibrary
'''
        self.assertEqual(finalize(text, self.adapter)[0], text)

    def test_scoped_helper_reference_is_rejected_without_confusing_other_library(self):
        text = WRAPPERS + '''library First
private function Shared takes integer kklc_parent returns nothing
endfunction
endlibrary
library Second
private function Shared takes nothing returns nothing
endfunction
function Late takes nothing returns nothing
call TimerStart(null,1,false,function Shared)
endfunction
endlibrary
'''
        self.assertEqual(finalize(text, self.adapter)[0], text)
        unsafe = text.replace('function Late', 'private function Late').replace(
            'call TimerStart(null,1,false,function Shared)',
            'call TimerStart(null,1,false,function First_Shared)')
        # A public alias is exported; a private name cannot be resolved externally.
        unsafe = unsafe.replace('private function Shared takes integer',
                                'public function Shared takes integer')
        with self.assertRaisesRegex(ValueError, 'code reference'):
            finalize(unsafe, self.adapter)

    def test_private_native_name_is_not_replaced_as_global_destruction(self):
        text = WRAPPERS + '''library Custom
private function DestroyTimer takes timer value returns nothing
call DoNothing()
endfunction
private function Init takes nothing returns nothing
call DestroyTimer(null)
endfunction
endlibrary
'''
        self.assertEqual(finalize(text, self.adapter)[0], text)

    def test_deferred_destruction_wraps_global_calls_once(self):
        text = WRAPPERS + '''function Late takes nothing returns nothing
    call DestroyTimer(CreateTimer())
    call DestroyTrigger(CreateTrigger())
endfunction
'''
        output, report = finalize(text, self.adapter)
        self.assertIn('call KKLC_DestroyTimer(CreateTimer())', output)
        self.assertIn('call KKLC_DestroyTrigger(CreateTrigger())', output)
        self.assertEqual(output.count('CreateTimer()'), 1)
        self.assertEqual(output.count('CreateTrigger()'), 1)
        self.assertEqual(report['destroy_calls_wrapped'], {'DestroyTimer': 1, 'DestroyTrigger': 1})
        self.assertIn(WRAPPERS, output)
        self.assertEqual(self.seen, [output])
        second, report2 = finalize(output, self.adapter)
        self.assertEqual(second, output)
        self.assertFalse(report2['changed'])

    def test_comments_strings_and_member_methods_are_untouched(self):
        text = WRAPPERS + '''function Late takes nothing returns nothing
    // call DestroyTimer(value)
    call BJDebugMsg("DestroyTrigger(value)")
    call widget.DestroyTimer()
endfunction
'''
        output, report = finalize(text, self.adapter)
        self.assertEqual(output, text)
        self.assertFalse(report['changed'])

    def test_missing_or_modified_wrapper_is_rejected(self):
        for text in (WRAPPERS.replace('call KKLC_Unbind(value)', 'call DoNothing()', 1),
                     WRAPPERS.replace('call DestroyTimer(value)', 'call KKLC_DestroyTimer(value)', 1),
                     WRAPPERS.replace('function KKLC_DestroyTimer', 'function Other', 1)):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'wrapper'):
                finalize(text, self.adapter)

    def test_direct_helper_call_is_allowed(self):
        text = WRAPPERS + HELPER + 'function Root takes nothing returns nothing\ncall Shared(1)\nendfunction\n'
        output, report = finalize(text, self.adapter)
        self.assertEqual(output, text)
        self.assertEqual(report['context_helpers'], ['Shared'])

    def test_deferred_bare_helper_code_reference_is_rejected(self):
        text = WRAPPERS + HELPER + 'function Late takes nothing returns nothing\ncall TimerStart(null,1,false,function Shared)\nendfunction\n'
        with self.assertRaisesRegex(ValueError, 'code reference.*Shared'):
            finalize(text, self.adapter)

    def test_deferred_executefunc_helper_and_escaped_targets_are_rejected(self):
        for target in ('"Shared"', '"Sha\\red"', '"Sha\\red" + "suffix"'):
            text = WRAPPERS + HELPER + 'function Late takes nothing returns nothing\ncall ExecuteFunc(' + target + ')\nendfunction\n'
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'ExecuteFunc'):
                finalize(text, self.adapter)

    def test_dynamic_executefunc_guard_dispatches_normal_target_and_blocks_helpers_and_bridges(self):
        from jass_runtime import translate_jass
        bridge = '''function Shared_KKLCBridge takes nothing returns nothing
call DoNothing()
endfunction
'''
        text = WRAPPERS + HELPER + bridge + '''function Late takes nothing returns nothing
call ExecuteFunc(targetName)
call ExecuteFunc("Sha" + "red")
endfunction
'''
        output, report = finalize(text, self.adapter)
        self.assertIn('call KKLC_ExecuteFuncGuarded(targetName)', output)
        self.assertIn('call KKLC_ExecuteFuncGuarded("Sha" + "red")', output)
        self.assertEqual(report['execute_func_guarded_calls'], 2)
        self.assertEqual(report['execute_func_protected_count'], 2)
        parsed = adapter.Adapter(output)
        parsed.parse(trusted_output=True)
        guard = parsed.functions['KKLC_ExecuteFuncGuarded']
        source = output[guard.start:guard.finish]
        self.assertLess(guard.start, parsed.functions['Shared'].start)
        dispatched, failed, prefixes = [], [], []
        def substring(value, start, end):
            prefixes.append(value)
            return value[start:end]
        namespace = {'ExecuteFunc': dispatched.append, 'KKLC_Fail': failed.append,
                     'SubString': substring}
        executable = translate_jass('globals\ninteger dummy = 0\nendglobals\n' + source)
        exec(compile(executable, '<actual-generated-executefunc-guard>', 'exec'), namespace)
        for target in ('Ordinary', 'Shared', 'Shared_KKLCBridge', 'Share', 'Other_Shared'):
            namespace['KKLC_ExecuteFuncGuarded'](target)
        self.assertEqual(dispatched, ['Ordinary', 'Share', 'Other_Shared'])
        self.assertEqual(failed, ['ExecuteFunc cannot supply helper context'] * 2)
        self.assertEqual(prefixes, ['Ordinary', 'Shared', 'Shared_KKLCBridge', 'Share', 'Other_Shared'])
        again, repeated = finalize(output, self.adapter)
        self.assertEqual(again, output)
        self.assertFalse(repeated['changed'])
        self.assertEqual(repeated['execute_func_guarded_calls'], 0)

    def test_existing_executefunc_guard_must_match_code_and_complete_protected_set(self):
        output, _ = finalize(WRAPPERS + HELPER + '''function Late takes nothing returns nothing
call ExecuteFunc(targetName)
endfunction
''', self.adapter)
        for changed in (
                output.replace('if target == "Shared" then', 'if target == "Different" then'),
                output + HELPER.replace('Shared', 'Other'),
                output.replace('call ExecuteFunc(target)', 'call DoNothing()')):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'ExecuteFunc guard'):
                finalize(changed, self.adapter)

    def test_computed_executefunc_remains_rejected_with_scoped_helpers(self):
        scoped = 'library Private\nprivate ' + HELPER + 'endlibrary\n'
        text = WRAPPERS + scoped + '''function Late takes nothing returns nothing
call ExecuteFunc(targetName)
endfunction
'''
        with self.assertRaisesRegex(ValueError, 'scoped context helpers'):
            finalize(text, self.adapter)

    def test_nonhelper_literal_executefunc_remains_allowed(self):
        text = WRAPPERS + HELPER + 'function Late takes nothing returns nothing\ncall ExecuteFunc("Ordinary")\nendfunction\n'
        self.assertEqual(finalize(text, self.adapter)[0], text)

    def test_dynamic_executefunc_without_helpers_remains_allowed(self):
        text = WRAPPERS + 'function Late takes nothing returns nothing\ncall ExecuteFunc(targetName)\nendfunction\n'
        self.assertEqual(finalize(text, self.adapter)[0], text)

    def test_callback_validation_failure_returns_no_candidate(self):
        def reject(text):
            self.assertIn('call KKLC_DestroyTimer(null)', text)
            raise ValueError('late registration rejected')
        self.adapter.validate_generated_callbacks = reject
        text = WRAPPERS + 'function Late takes nothing returns nothing\ncall DestroyTimer(null)\nendfunction\n'
        with self.assertRaisesRegex(ValueError, 'late registration rejected'):
            finalize(text, self.adapter)

    def test_later_jasshelper_directives_are_rejected(self):
        for directive in ('inject main', 'import "late.j"', 'external tool', 'novjass'):
            with self.subTest(directive=directive), self.assertRaisesRegex(ValueError, 'JassHelper directive'):
                finalize(WRAPPERS + '\n//! ' + directive + '\n', self.adapter)

    def test_literal_textmacro_expands_and_generated_destruction_is_wrapped(self):
        text = WRAPPERS + '''//! textmacro Cleanup takes NAME, VALUE
function $NAME$ takes nothing returns nothing
call DestroyTimer($VALUE$)
endfunction
//! endtextmacro
//! runtextmacro Cleanup("First", "null")
//! runtextmacro Cleanup ("Second", "CreateTimer()")
'''
        output, report = finalize(text, self.adapter)
        self.assertIn('function First takes nothing', output)
        self.assertIn('function Second takes nothing', output)
        self.assertIn('call KKLC_DestroyTimer(null)', output)
        self.assertIn('call KKLC_DestroyTimer(CreateTimer())', output)
        self.assertNotIn('textmacro', output)
        self.assertEqual(report['textmacros'], {'definitions': 1, 'invocations': 2})
        self.assertEqual(report['destroy_calls_wrapped']['DestroyTimer'], 2)
        self.assertTrue(report['changed'])
        again, repeat = finalize(output, self.adapter)
        self.assertEqual(again, output)
        self.assertFalse(repeat['changed'])

    def test_textmacro_generated_helper_reference_is_checked_in_jass_and_zinc(self):
        for body in (
                'function Late takes nothing returns nothing\ncall TimerStart(null,1,false,function $TARGET$)\nendfunction\n',
                '//! zinc\nlibrary Late { function Init() { TimerStart(null,1,false,$TARGET$); } }\n//! endzinc\n'):
            text = WRAPPERS + HELPER + '//! textmacro Register takes TARGET\n' + body + '''//! endtextmacro
//! runtextmacro Register("Shared")
'''
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'helper context|code reference'):
                finalize(text, self.adapter)

    def test_textmacro_directives_in_strings_and_comments_do_not_activate(self):
        text = WRAPPERS + '''/*
//! textmacro Fake
//! runtextmacro Fake()
//! endtextmacro
*/
// Example: //! runtextmacro Missing()
function Late takes nothing returns nothing
call BJDebugMsg("//! runtextmacro Missing()")
endfunction
'''
        output, report = finalize(text, self.adapter)
        self.assertEqual(output, text)
        self.assertEqual(report['textmacros'], {'definitions': 0, 'invocations': 0})

    def test_textmacro_rejects_unknown_dynamic_escaped_and_wrong_arity_arguments(self):
        definition = '//! textmacro Known takes ARG\ncall DoNothing()\n//! endtextmacro\n'
        for invocation in ('Missing()', 'Known(value)', 'Known("a" + "b")',
                           'Known("escaped\\n")', 'Known()', 'Known("a", "b")'):
            with self.subTest(invocation=invocation), self.assertRaisesRegex(ValueError, 'textmacro|Textmacro'):
                finalize(WRAPPERS + definition + '//! runtextmacro ' + invocation + '\n', self.adapter)

    def test_textmacro_rejects_unclosed_nested_recursive_and_duplicate_definitions(self):
        for macro in (
                '//! textmacro Open\ncall DoNothing()\n',
                '//! textmacro Outer\n//! textmacro Inner\n//! endtextmacro\n//! endtextmacro\n',
                '//! textmacro Recursive\n//! runtextmacro Recursive()\n//! endtextmacro\n',
                '//! textmacro Same\n//! endtextmacro\n//! textmacro Same\n//! endtextmacro\n',
                '//! textmacro Bad takes A, A\n//! endtextmacro\n',
                '//! textmacro Bad takes A\ncall $B$()\n//! endtextmacro\n',
                '//! endtextmacro\n'):
            with self.subTest(macro=macro), self.assertRaisesRegex(ValueError, 'textmacro'):
                finalize(WRAPPERS + macro, self.adapter)

    def test_textmacro_cannot_defer_another_generation_stage(self):
        text = WRAPPERS + '''//! textmacro Import
//! import "late.j"
//! endtextmacro
//! runtextmacro Import()
'''
        with self.assertRaisesRegex(ValueError, 'JassHelper directive'):
            finalize(text, self.adapter)

    def test_ordinary_bang_comments_and_zinc_preserve_code_and_wrap_destruction(self):
        text = WRAPPERS + '''//! Check Formula
//!取反似乎无效，用一点笨比写法
//! zinc
library Existing {
function cleanup(timer t) { DestroyTimer(t); }
}
//! endzinc
'''
        output, report = finalize(text, self.adapter)
        self.assertEqual(output, text.replace('DestroyTimer(t);', 'KKLC_DestroyTimer(t);'))
        self.assertEqual(report['destroy_calls_wrapped']['DestroyTimer'], 1)

    def test_zinc_bare_helper_reference_and_native_shadowing_are_rejected(self):
        for body, reason in (
            ('function init() { TimerStart(null,1,false,Shared); }', 'helper context'),
            ('function DestroyTimer(timer t) {}', 'native shadowing'),
        ):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, reason):
                finalize(WRAPPERS + HELPER + '\n//! zinc\n' + body + '\n//! endzinc\n', self.adapter)

    def test_zinc_member_declaration_and_call_keep_the_same_name(self):
        text = WRAPPERS + '''//! zinc
library Existing {
struct Custom {
method DestroyTimer(timer value) {}
}
function cleanup(Custom value) { value.DestroyTimer(null); }
}
//! endzinc
'''
        self.assertEqual(finalize(text, self.adapter)[0], text)

    def test_unclosed_zinc_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unclosed Zinc'):
            finalize(WRAPPERS + '\n//! zinc\n', self.adapter)

    def test_directive_spelling_in_ordinary_comments_and_strings_is_allowed(self):
        text = WRAPPERS + '''/*
//! runtextmacro NotActive()
*/
// Example: //! inject main
function Late takes nothing returns nothing
    call BJDebugMsg("//! runtextmacro NotActive()")
endfunction
'''
        self.assertEqual(finalize(text, self.adapter)[0], text)


if __name__ == '__main__':
    unittest.main()
