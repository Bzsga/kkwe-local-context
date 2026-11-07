import unittest

from jass_runtime import Handle, JassRuntime, translate_jass


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.r = JassRuntime()

    def test_more_than_8192_live_contexts_use_six_tables(self):
        ids = [self.r.KKLC_Alloc() for _ in range(9000)]
        self.assertEqual(len(set(ids)), 9000)
        self.assertTrue(all(self.r.KKLC_Alive(ctx) for ctx in ids))
        self.assertEqual(len(self.r.tables), 6)
        for ctx in ids:
            self.r.KKLC_Release(ctx)
        self.assertFalse(any(self.r.KKLC_Alive(ctx) for ctx in ids))

    def test_refcounts_and_recycling_clear_values(self):
        ctx = self.r.KKLC_Alloc()
        self.r.save("KKLC_Data", ctx, 1, 99)
        self.r.KKLC_Retain(ctx)
        self.r.KKLC_Release(ctx)
        self.assertTrue(self.r.KKLC_Alive(ctx))
        self.r.KKLC_Release(ctx)
        self.assertFalse(self.r.KKLC_Alive(ctx))
        reused = self.r.KKLC_Alloc()
        self.assertEqual(reused, ctx)
        self.assertEqual(self.r.load("KKLC_Data", reused, 1), 0)

    def test_array_namespace_exact_signed_indices_and_cleanup(self):
        ctx = self.r.KKLC_Alloc()
        self.r.save("KKLC_Data", ctx, 7, 101)
        self.assertEqual(self.r.KKLC_Array(ctx, 7, False), 0)
        page = self.r.KKLC_Array(ctx, 7, True)
        other = self.r.KKLC_Array(ctx, 8, True)
        indices = [-2147483648, -1, 0, 1, 8192, 2147483647]
        for i in indices:
            self.r.KKLC_Touch(page, i)
            self.r.save("KKLC_Arrays", page, i, i)
        self.r.KKLC_Touch(page, 0)
        node = self.r.KKLC_IndexFirst(page)
        found = []
        while node:
            found.append(self.r.KKLC_IndexValue(node))
            node = self.r.KKLC_IndexNext(node)
        self.assertCountEqual(found, indices)
        self.assertEqual(self.r.load("KKLC_Data", ctx, 7), 101)
        self.assertEqual(self.r.load("KKLC_Arrays", other, 1), 0)
        self.r.KKLC_Release(ctx)
        for name in ["KKLC_Data", "KKLC_Arrays", "KKLC_ArrayMap", "KKLC_IndexMap"]:
            self.assertFalse(self.r.ns[name])

    def test_owner_unbind_does_not_release_running_callback(self):
        timer = Handle(100)
        owner = self.r.KKLC_Owner(timer)
        active = self.r.KKLC_AcquireOwner(timer)
        self.assertEqual(owner, active)
        self.r.save("KKLC_Data", owner, 1, 42)
        self.r.KKLC_Unbind(timer)
        self.assertTrue(self.r.KKLC_Alive(active))
        reused_handle = Handle(100)
        new_owner = self.r.KKLC_Owner(reused_handle)
        self.assertNotEqual(new_owner, active)
        self.assertEqual(self.r.load("KKLC_Data", active, 1), 42)
        self.assertEqual(self.r.load("KKLC_Data", new_owner, 1), 0)
        self.r.KKLC_Release(active)
        self.assertFalse(self.r.KKLC_Alive(active))
        self.r.KKLC_Unbind(reused_handle)

    def test_named_pages_share_only_their_own_persistent_context(self):
        first = self.r.KKLC_NamedOwner(1)
        second = self.r.KKLC_NamedOwner(2)
        self.assertNotEqual(first, second)
        self.r.save("KKLC_Data", first, 7, 11)
        self.r.save("KKLC_Data", second, 7, 22)
        for _ in range(3):
            self.assertEqual(self.r.KKLC_NamedOwner(1), first)
            self.assertEqual(self.r.load("KKLC_Data", self.r.KKLC_NamedOwner(1), 7), 11)
            self.assertEqual(self.r.load("KKLC_Data", self.r.KKLC_NamedOwner(2), 7), 22)
        self.assertEqual(self.r.refs(first), 1)
        self.assertEqual(self.r.refs(second), 1)
        self.assertEqual(len(self.r.tables), 6)
        self.assertFalse(self.r.messages)

    def test_named_page_clear_preserves_identity_and_separates_scalars_arrays_handles(self):
        named = self.r.KKLC_NamedOwner(1)
        other = self.r.KKLC_NamedOwner(2)
        target = Handle(1)  # Same integer as a page ID, in another table row.
        owner = self.r.KKLC_Owner(target)
        self.assertEqual(len({named, other, owner}), 3)
        self.r.save("KKLC_Data", named, 7, 11)
        self.r.save("KKLC_Data", other, 7, 22)
        self.r.save("KKLC_Data", owner, 7, 33)
        page = self.r.KKLC_Array(named, 7, True)
        for index in [-2147483648, 0, 2147483647]:
            self.assertTrue(self.r.KKLC_Touch(page, index))
            self.r.save("KKLC_Arrays", page, index, 44)
        self.assertEqual(self.r.load("KKLC_Data", named, 7), 11)
        self.assertEqual(self.r.load("KKLC_Arrays", page, 0), 44)

        self.r.KKLC_ClearContext(self.r.KKLC_NamedOwner(1))
        self.assertEqual(self.r.KKLC_NamedOwner(1), named)
        self.assertEqual(self.r.refs(named), 1)
        self.assertEqual(self.r.load("KKLC_Data", named, 7), 0)
        self.assertEqual(self.r.KKLC_Array(named, 7, False), 0)
        self.assertEqual(self.r.load("KKLC_Data", other, 7), 22)
        self.assertEqual(self.r.load("KKLC_Data", owner, 7), 33)
        self.r.save("KKLC_Data", named, 7, 55)
        replacement = self.r.KKLC_Array(named, 7, True)
        self.assertEqual(self.r.load("KKLC_Arrays", replacement, 0), 0)
        self.r.KKLC_Unbind(target)
        self.assertEqual(self.r.KKLC_NamedOwner(1), named)
        self.assertEqual(self.r.load("KKLC_Data", named, 7), 55)
        self.assertEqual(self.r.load("KKLC_Data", other, 7), 22)
        self.assertFalse(self.r.messages)

    def test_named_page_invalid_or_exhausted_allocation_does_not_publish_context(self):
        self.assertEqual(self.r.KKLC_NamedOwner(0), 0)
        self.assertEqual(self.r.KKLC_NamedOwner(-1), 0)
        self.assertEqual(self.r.KKLC_NextId, 0)
        self.r.ns["KKLC_NextId"] = 2147483647
        self.assertEqual(self.r.KKLC_NamedOwner(1), 0)
        self.assertEqual(self.r.load("KKLC_Owners", 0, 1), 0)
        self.assertEqual(self.r.messages, ["KKLC: logical ID space exhausted"])

    def test_clear_context_preserves_held_refs_and_recycles_only_array_storage(self):
        ctx = self.r.KKLC_Alloc()
        held = self.r.KKLC_Retain(ctx)
        other = self.r.KKLC_Alloc()
        self.r.save("KKLC_Data", ctx, 1, 77)
        unit = Handle(401)
        self.r.ns["SaveUnitHandle"](self.r.KKLC_Data, ctx, 2, unit)
        self.r.save("KKLC_Data", other, 1, 99)
        array_storage = set()
        for symbol in [1, 2]:
            page = self.r.KKLC_Array(ctx, symbol, True)
            array_storage.add(page)
            for index in [-2147483648, 0]:
                self.r.KKLC_Touch(page, index)
                self.r.save("KKLC_Arrays", page, index, 33)
                array_storage.add(self.r.load("KKLC_IndexMap", page, index))

        self.r.KKLC_ClearContext(ctx)
        self.r.KKLC_ClearContext(ctx)  # Repeated clearing must not double-free nodes.
        self.assertEqual(held, ctx)
        self.assertTrue(self.r.KKLC_Alive(ctx))
        self.assertEqual(self.r.refs(ctx), 2)
        self.assertEqual(self.r.load("KKLC_Meta", ctx, 2), 0)
        self.assertEqual(self.r.load("KKLC_Data", held, 1), 0)
        self.assertIsNone(self.r.ns["LoadUnitHandle"](self.r.KKLC_Data, held, 2))
        self.assertEqual(self.r.load("KKLC_Data", other, 1), 99)
        self.assertFalse(self.r.KKLC_Arrays)
        self.assertFalse(self.r.KKLC_ArrayMap)
        self.assertFalse(self.r.KKLC_IndexMap)

        reused = [self.r.KKLC_Alloc() for _ in array_storage]
        self.assertEqual(set(reused), array_storage)
        self.assertEqual(len(set(reused)), len(reused))
        self.assertNotIn(ctx, reused)
        self.r.save("KKLC_Data", held, 1, 88)
        replacement = self.r.KKLC_Array(held, 1, True)
        self.assertTrue(self.r.KKLC_Touch(replacement, 0))
        self.r.save("KKLC_Arrays", replacement, 0, 44)
        self.r.KKLC_Release(ctx)
        self.assertTrue(self.r.KKLC_Alive(held))
        self.assertEqual(self.r.load("KKLC_Data", held, 1), 88)
        self.assertEqual(self.r.load("KKLC_Arrays", replacement, 0), 44)
        self.r.KKLC_Release(held)
        for allocated in reused + [other]:
            self.r.KKLC_Release(allocated)
        self.assertFalse(any(self.r.load("KKLC_Meta", node, 0) for node in range(1, self.r.KKLC_NextId + 1)))
        self.assertFalse(self.r.messages)

    def test_clear_owner_keeps_dynamic_marker_binding_and_active_references(self):
        target = Handle(402)
        self.r.KKLC_MarkDynamic(target)
        prepared = self.r.KKLC_Prepare(target)
        held = self.r.KKLC_AcquireOwner(target)
        self.r.save("KKLC_Data", held, 1, 77)
        self.assertEqual(self.r.refs(held), 3)

        self.r.KKLC_ClearOwner(target)
        self.assertEqual(self.r.KKLC_Owner(target), held)
        self.assertTrue(self.r.KKLC_IsDynamic(target))
        self.assertEqual(self.r.refs(held), 3)
        self.assertEqual(self.r.load("KKLC_Data", prepared, 1), 0)
        self.r.save("KKLC_Data", held, 1, 88)
        self.r.KKLC_ClearContext(held)
        self.assertEqual(self.r.load("KKLC_Data", prepared, 1), 0)
        self.assertTrue(self.r.KKLC_IsDynamic(target))
        self.r.KKLC_Release(held)
        self.r.KKLC_Release(prepared)
        self.assertEqual(self.r.refs(self.r.KKLC_Owner(target)), 1)
        self.r.KKLC_Unbind(target)
        self.assertFalse(self.r.KKLC_Alive(held))
        self.assertFalse(self.r.KKLC_IsDynamic(target))
        self.assertFalse(self.r.messages)

    def test_clear_owner_does_not_allocate_or_clear_detached_reused_handle_state(self):
        target = Handle(403)
        self.r.KKLC_MarkDynamic(target)
        before = self.r.KKLC_NextId
        self.r.KKLC_ClearOwner(target)
        self.r.KKLC_ClearOwner(None)
        self.r.KKLC_ClearContext(0)
        self.assertEqual(self.r.KKLC_NextId, before)
        self.assertTrue(self.r.KKLC_IsDynamic(target))
        self.assertEqual(self.r.load("KKLC_Owners", target.id, 0), 0)

        held = self.r.KKLC_AcquireOwner(target)
        self.r.save("KKLC_Data", held, 1, 77)
        self.r.KKLC_Unbind(target)
        replacement = self.r.KKLC_Owner(Handle(target.id))
        self.assertNotEqual(replacement, held)
        self.r.save("KKLC_Data", replacement, 1, 99)
        self.r.KKLC_ClearOwner(Handle(target.id))
        self.assertEqual(self.r.load("KKLC_Data", replacement, 1), 0)
        self.assertEqual(self.r.load("KKLC_Data", held, 1), 77)
        self.assertEqual(self.r.refs(held), 1)
        self.r.KKLC_ClearContext(held)
        self.assertEqual(self.r.load("KKLC_Data", held, 1), 0)
        self.r.KKLC_Release(held)
        self.r.KKLC_Unbind(target)
        self.assertFalse(self.r.KKLC_Alive(held))
        self.assertFalse(self.r.KKLC_Alive(replacement))
        self.assertFalse(self.r.messages)

    def test_clear_array_removes_only_requested_page_and_reuse_is_empty(self):
        ctx = self.r.KKLC_Alloc()
        first = self.r.KKLC_Array(ctx, 1, True)
        second = self.r.KKLC_Array(ctx, 2, True)
        for page in [first, second]:
            self.assertTrue(self.r.KKLC_Touch(page, -1))
            self.r.save("KKLC_Arrays", page, -1, 19)
        self.r.save("KKLC_Data", ctx, 1, 31)
        self.r.KKLC_ClearArray(ctx, 1)
        self.assertTrue(self.r.KKLC_Alive(ctx))
        self.assertEqual(self.r.KKLC_Array(ctx, 1, False), 0)
        self.assertEqual(self.r.load("KKLC_Arrays", second, -1), 19)
        replacement = self.r.KKLC_Array(ctx, 1, True)
        self.assertEqual(self.r.load("KKLC_Arrays", replacement, -1), 0)
        self.assertEqual(self.r.KKLC_IndexFirst(replacement), 0)
        self.assertEqual(self.r.load("KKLC_Data", ctx, 1), 31)
        self.r.KKLC_ClearArray(ctx, 2)
        self.r.KKLC_ClearArray(ctx, 1)
        self.assertEqual(self.r.load("KKLC_Meta", ctx, 2), 0)
        self.r.KKLC_Release(ctx)
        self.assertFalse(self.r.KKLC_Arrays)

    def test_pending_nested_and_condition_reentry(self):
        seen = []
        target = Handle(111)
        inside_condition = False

        def action():
            local_ctx = self.r.KKLC_EnterRoot(target)
            seen.append(self.r.load("KKLC_Data", local_ctx, 1))
            self.r.KKLC_Release(local_ctx)

        def condition():
            nonlocal inside_condition
            if not inside_condition:
                inside_condition = True
                nested = self.r.KKLC_Prepare(target)
                self.r.save("KKLC_Data", nested, 1, 20)
                self.r.KKLC_Execute(target, False, nested)
                inside_condition = False
            return True

        target.action = action
        target.condition = condition
        outer = self.r.KKLC_Prepare(target)
        self.r.save("KKLC_Data", outer, 1, 10)
        self.r.KKLC_Execute(target, True, outer)
        self.assertEqual(seen, [20, 10])
        self.assertEqual(self.r.KKLC_PendingCtx, 0)
        self.assertIsNone(self.r.KKLC_PendingTarget)
        self.assertFalse(self.r.KKLC_Alive(outer))

    def test_false_condition_and_no_entry_release_prepared_reference(self):
        for target in [Handle(112, condition=lambda: False), Handle(113)]:
            ctx = self.r.KKLC_Prepare(target)
            self.r.KKLC_Execute(target, True, ctx)
            self.assertFalse(self.r.KKLC_Alive(ctx))
            self.assertEqual(self.r.KKLC_PendingCtx, 0)

    def test_two_yielded_bodies_keep_true_local_contexts(self):
        waiting = []
        seen = []
        target = Handle(114)

        def action():
            local_ctx = self.r.KKLC_EnterRoot(target)
            # Engine yield is modeled by retaining a continuation holding the
            # exact local acquired by the actual runtime entry function.
            def resume():
                seen.append(self.r.load("KKLC_Data", local_ctx, 1))
                self.r.KKLC_Release(local_ctx)
            waiting.append((local_ctx, resume))

        target.action = action
        for value in [11, 22]:
            ctx = self.r.KKLC_Prepare(target)
            self.r.save("KKLC_Data", ctx, 1, value)
            self.r.KKLC_Execute(target, False, ctx)
            self.assertTrue(self.r.KKLC_Alive(ctx))
            self.assertEqual(self.r.KKLC_PendingCtx, 0)
        waiting[1][1]()
        waiting[0][1]()
        self.assertEqual(seen, [22, 11])
        self.assertFalse(any(self.r.KKLC_Alive(ctx) for ctx, _ in waiting))

    def test_dynamic_condition_callback_preserves_owner_state(self):
        target = Handle(115)
        self.r.KKLC_MarkDynamic(target)
        owner = self.r.KKLC_Owner(target)

        def callback():
            local_ctx = self.r.KKLC_AcquireOwner(target)
            self.r.save("KKLC_Data", local_ctx, 1, self.r.load("KKLC_Data", local_ctx, 1) + 1)
            self.r.KKLC_Release(local_ctx)
            return True

        target.condition = callback
        for check in [False, True]:
            prepared = self.r.KKLC_Prepare(target)
            self.assertEqual(prepared, owner)
            self.r.KKLC_Execute(target, check, prepared)
        self.assertEqual(self.r.load("KKLC_Data", owner, 1), 2)
        self.assertEqual(self.r.refs(owner), 1)
        self.r.KKLC_Unbind(target)
        self.assertFalse(self.r.KKLC_IsDynamic(target))
        self.assertFalse(self.r.KKLC_Alive(owner))

    def test_bridges_are_nested_owned_and_fail_closed_on_bad_pop(self):
        a, b = self.r.KKLC_Alloc(), self.r.KKLC_Alloc()
        outer = self.r.KKLC_BridgePush(a)
        inner = self.r.KKLC_BridgePush(b)
        self.r.KKLC_BridgePop(outer)
        self.assertEqual(self.r.KKLC_BridgeHead, inner)
        acquired = self.r.KKLC_BridgeAcquire()
        self.assertEqual(acquired, b)
        self.r.KKLC_BridgePop(inner)
        self.r.KKLC_Release(acquired)
        acquired = self.r.KKLC_BridgeAcquire()
        self.assertEqual(acquired, a)
        self.r.KKLC_BridgePop(outer)
        self.r.KKLC_Release(acquired)
        self.assertEqual(self.r.KKLC_BridgeHead, 0)
        self.r.KKLC_Release(a)
        self.r.KKLC_Release(b)

    def test_invalid_contexts_exhaustion_and_refcount_overflow(self):
        self.assertFalse(self.r.KKLC_Alive(0))
        self.assertEqual(self.r.KKLC_Retain(123), 0)
        self.r.KKLC_Release(123)
        self.r.ns["KKLC_NextId"] = 2147483647
        self.assertEqual(self.r.KKLC_Alloc(), 0)
        self.assertEqual(self.r.KKLC_NextId, 2147483647)
        self.r.ns["KKLC_NextId"] = 0
        ctx = self.r.KKLC_Alloc()
        self.r.save("KKLC_Meta", ctx, 1, 2147483647)
        self.assertEqual(self.r.KKLC_Retain(ctx), 0)
        self.assertEqual(self.r.refs(ctx), 2147483647)

    def test_array_allocation_and_index_exhaustion_fail_closed(self):
        ctx = self.r.KKLC_Alloc()
        self.r.ns["KKLC_NextId"] = 2147483647
        self.assertEqual(self.r.KKLC_Array(ctx, 1, True), 0)
        self.assertEqual(self.r.KKLC_Array(0, 1, True), 0)
        self.assertFalse(self.r.KKLC_Touch(0, 5))
        self.r.ns["KKLC_NextId"] = 1
        page = self.r.KKLC_Array(ctx, 1, True)
        self.r.ns["KKLC_NextId"] = 2147483647
        self.assertFalse(self.r.KKLC_Touch(page, 5))
        self.assertEqual(self.r.KKLC_IndexFirst(page), 0)
        self.assertFalse(self.r.KKLC_Arrays)
        self.r.KKLC_Release(ctx)

    def test_hashtable_capacity_failure_is_not_retried_or_used(self):
        attempts = 0

        def capacity_limited_init():
            nonlocal attempts
            attempts += 1
            return {} if attempts < 3 else None

        self.r.ns["InitHashtable"] = capacity_limited_init
        self.assertEqual(self.r.KKLC_Alloc(), 0)
        self.assertEqual(self.r.KKLC_Alloc(), 0)
        self.assertEqual(attempts, 6)
        self.assertFalse(self.r.KKLC_Ready)
        self.assertFalse(self.r.KKLC_Alive(1))
        self.assertEqual(self.r.KKLC_Owner(Handle(1)), 0)
        self.r.KKLC_Unbind(Handle(1))
        self.assertFalse(self.r.KKLC_IsDynamic(Handle(1)))
        self.assertEqual(len(self.r.messages), 1)

    def test_dynamic_destroy_during_callback_keeps_prepared_reference(self):
        target = Handle(116)
        self.r.KKLC_MarkDynamic(target)
        prepared = self.r.KKLC_Prepare(target)

        def callback():
            local_ctx = self.r.KKLC_AcquireOwner(target)
            self.r.KKLC_Unbind(target)
            self.r.KKLC_Release(local_ctx)
            self.assertTrue(self.r.KKLC_Alive(prepared))
            self.assertEqual(self.r.refs(prepared), 1)
            return False

        target.condition = callback
        self.r.KKLC_Execute(target, False, prepared)
        self.assertFalse(self.r.KKLC_Alive(prepared))

    def test_action_nested_dispatch_restores_pending_without_aba(self):
        outer_target, inner_target = Handle(117), Handle(118)
        seen = []

        def inner_action():
            local_ctx = self.r.KKLC_EnterRoot(inner_target)
            seen.append(self.r.load("KKLC_Data", local_ctx, 1))
            self.r.KKLC_Release(local_ctx)

        def outer_action():
            local_ctx = self.r.KKLC_EnterRoot(outer_target)
            for value in [20, 30]:
                nested = self.r.KKLC_Prepare(inner_target)
                self.r.save("KKLC_Data", nested, 1, value)
                self.r.KKLC_Execute(inner_target, False, nested)
            seen.append(self.r.load("KKLC_Data", local_ctx, 1))
            self.r.KKLC_Release(local_ctx)

        outer_target.action, inner_target.action = outer_action, inner_action
        outer = self.r.KKLC_Prepare(outer_target)
        self.r.save("KKLC_Data", outer, 1, 10)
        self.r.KKLC_Execute(outer_target, False, outer)
        self.assertEqual(seen, [20, 30, 10])
        self.assertEqual(self.r.KKLC_PendingCtx, 0)
        self.assertFalse(self.r.KKLC_Alive(outer))

    def test_translator_rejects_unknown_syntax(self):
        with self.assertRaises(ValueError):
            translate_jass("globals\ninteger array bogus\nendglobals")


if __name__ == "__main__":
    unittest.main()
