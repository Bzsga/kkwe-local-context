"""Two-client source-execution checks for the runtime's allocation boundary.

These execute the actual JASS runtime through the existing native mocks. They
verify InitHashtable call traces and local lookup semantics, not Warcraft's
handle allocator, network checksums, scheduling, or multiplayer desync behavior.
The compiler tests separately verify that startup invokes KKLC_Init globally.
"""
import unittest

from jass_runtime import Handle, JassRuntime


class TwoClientRuntimeTests(unittest.TestCase):
    @staticmethod
    def client():
        calls = []

        def init_table():
            calls.append("InitHashtable")
            return {}

        runtime = JassRuntime(native_overrides={"InitHashtable": init_table})
        return runtime, calls

    def test_synchronized_initialization_is_idempotent_on_both_clients(self):
        for runtime, calls in (self.client(), self.client()):
            self.assertEqual(calls, ["InitHashtable"] * 6)
            names = ("Data", "Arrays", "Meta", "ArrayMap", "IndexMap", "Owners")
            tables = [getattr(runtime, "KKLC_" + name) for name in names]
            runtime.KKLC_Init()
            runtime.KKLC_Init()
            runtime.KKLC_Init()
            self.assertTrue(runtime.KKLC_Ready)
            self.assertEqual(calls, ["InitHashtable"] * 6)
            self.assertEqual(len({id(table) for table in tables}), 6)
            self.assertTrue(all(table is getattr(runtime, "KKLC_" + name)
                                for name, table in zip(names, tables)))

    def test_one_client_named_page_and_null_unbind_create_no_more_handles(self):
        a, calls_a = self.client()
        b, calls_b = self.client()

        # A local UI callback runs only on A. Null destruction wrappers also
        # reach Unbind. Even before the library initializer checks readiness,
        # global initialization must already have created all native handles.
        ctx = a.KKLC_NamedOwner(7)
        a.save("KKLC_Data", ctx, 1, 123)
        a.KKLC_Unbind(None)
        self.assertEqual(a.load("KKLC_Data", a.KKLC_NamedOwner(7), 1), 123)
        self.assertEqual(b.load("KKLC_Owners", 0, 7), 0)
        self.assertEqual(calls_a, ["InitHashtable"] * 6)
        self.assertEqual(calls_b, calls_a)

    def test_different_opaque_ids_preserve_each_clients_owner_mapping(self):
        a, calls_a = self.client()
        b, calls_b = self.client()
        a.KKLC_Init()
        b.KKLC_Init()
        a.KKLC_NamedOwner(99)  # Persistent local UI state exists only on A.

        handle_a, handle_b = Handle(100), Handle(100)
        ctx_a = a.KKLC_Owner(handle_a)
        ctx_b = b.KKLC_Owner(handle_b)
        self.assertNotEqual(ctx_a, ctx_b)
        for runtime, owner, ctx in ((a, handle_a, ctx_a), (b, handle_b, ctx_b)):
            runtime.save("KKLC_Data", ctx, 1, 42)
            self.assertEqual(runtime.KKLC_Owner(owner), ctx)
            self.assertEqual(runtime.load("KKLC_Data", runtime.KKLC_Owner(owner), 1), 42)
            runtime.KKLC_Unbind(owner)
            self.assertFalse(runtime.KKLC_Alive(ctx))
        self.assertEqual(calls_a, ["InitHashtable"] * 6)
        self.assertEqual(calls_b, calls_a)
        self.assertFalse(a.messages or b.messages)


if __name__ == "__main__":
    unittest.main()
