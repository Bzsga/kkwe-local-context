# KKLC runtime contract (v1)

`kklc_runtime.j` is plain JASS, inserted before transformed user functions. It uses six fixed hashtables and logical IDs; no per-call hashtable creation or native JASS arrays. Public data tables are `KKLC_Data` (scalar: ctx, symbol) and `KKLC_Arrays` (array: page, exact signed index). Call `KKLC_Init()` before use; public allocation/lookup entry points initialize lazily too.

## Ownership and API

| API | Contract |
| --- | --- |
| `KKLC_Alloc() -> integer` | New context with one owned reference, or 0 on exhausted ID space. |
| `KKLC_Alive(ctx) -> boolean` | True only for an allocated context, not an array/list node. |
| `KKLC_Retain(ctx) -> integer` | Adds a reference and returns ctx; invalid input returns 0. |
| `KKLC_ClearContext(ctx)` | Clears all scalar values, array pages and index nodes while preserving the context identity, its reference count, and any owner binding/dynamic marker. Existing holders observe cleared data and can write again. 0/inactive input is harmless. Borrowed page/index IDs become invalid after clearing. |
| `KKLC_Release(ctx)` | Drops one reference; last reference clears scalars, array pages/index lists and recycles IDs. 0 is harmless. |
| `KKLC_Owner(handle) -> integer` | Finds/creates persistent owner context; returned ID is borrowed. Binding holds one reference. Null returns 0. |
| `KKLC_NamedOwner(page) -> integer` | Finds/creates persistent named-page context by a positive compile-time page ID; returned ID is borrowed. Its table entry holds one reference for the map lifetime. The same page shares values across callbacks; different pages and handle owners are separate. Invalid page/allocation failure returns 0. |
| `KKLC_AcquireOwner(handle) -> integer` | Owner plus a caller-owned reference. Callback stores result in a true local, then releases at every exit. |
| `KKLC_ClearOwner(handle)` | Clears the currently bound context without creating an owner, dropping references, unbinding, or removing the dynamic marker. Null/unbound handle is harmless. |
| `KKLC_Unbind(handle)` | Removes binding and dynamic marker, releases binding reference. Active callbacks remain valid until their own release. Call before destroying the handle. |
| `KKLC_MarkDynamic(trigger)` / `KKLC_IsDynamic(trigger)` | Marks condition-based generated dynamic trigger; marker remains until Unbind. |
| `KKLC_Array(ctx, symbol, create) -> integer` | Returns context-owned array page. Missing read returns 0 without allocation. No caller release for page. |
| `KKLC_ClearArray(ctx, symbol)` | Removes that page and its index nodes while retaining ctx and other arrays/scalars. For full capture snapshots, clear destination before copying; identical source/destination must return without clearing. |
| `KKLC_Touch(page, index) -> boolean` | Call before each array write (including handle-null removal) to track exact signed indices for capture copying. Save only when true; false means invalid page or allocator exhaustion. |
| `KKLC_IndexFirst(page) -> integer` | First opaque index node or 0. |
| `KKLC_IndexNext(node) -> integer` / `KKLC_IndexValue(node) -> integer` | Iterate touched indices; node sentinel 0 allows every signed index, including 0 and -2147483648. |
| `KKLC_Prepare(trigger) -> integer` | Ordinary trigger: new owned context. Marked dynamic trigger: retained owner context, preserving legacy shared persistent values. |
| `KKLC_EnterRoot(trigger) -> integer` | Immediately claims matching published pending reference; otherwise allocates a new owned context. Call as first generated entry step, before user code. |
| `KKLC_Execute(trigger, check, ctx)` | Consumes caller's prepared reference. Ordinary: optional TriggerEvaluate, then publishes pending immediately before TriggerExecute. Entry claims it, so waiting body keeps it after Execute returns. If no entry consumes it, Execute releases it. Dynamic: condition callback dispatches via TriggerEvaluate; prepared owner reference is released after dispatch. |
| `KKLC_BridgePush(ctx) -> integer` | Retains ctx and pushes a short synchronous callback bridge; returns opaque token, or 0 on failure with the previous bridge unchanged. Caller must stop before invoking the native callback when 0 is returned, or that callback could acquire an outer context. No wait between push/pop. |
| `KKLC_BridgeAcquire() -> integer` | Returns owned reference to current bridge context; callback immediately saves it in true local and releases on exit. |
| `KKLC_BridgePop(token)` | LIFO pop only; releases bridge reference. Invalid/out-of-order pop reports failure, does not corrupt stack. |

Scalar capture is generated typed Save/Load with collision-free compile-time symbol IDs. Array capture is generated typed copying: obtain source page without creating, iterate IndexFirst/Next/Value, Touch destination page, then typed Save/Load. Do not expose runtime metadata or use raw StringHash(name)+index.

## Required generator/native boundaries

- Locals always read/write the function-local ctx, not a long-lived global current context. Synchronous generated helpers may receive ctx explicitly; zero-argument callbacks use the short bridge above.
- Pending is published only at actual dispatch, after ordinary conditions finish. Parameter expressions can nest calls without stealing frames. Conditions that require prepared parameters are outside this v1 contract; adapter must reject unsupported use instead of silently changing behavior.
- Ordinary TriggerExecute must synchronously enter its generated action before returning or yielding. The entry consumes pending before any user statement. This native scheduling assumption is separately required for game validation; the offline native mock makes it explicit.
- Generated dynamic triggers execute their body as a Condition callback and retain owner state. Their callback uses AcquireOwner and releases on exit. The dynamic Execute path invokes TriggerEvaluate once. It does not convert persistent variables into per-invocation isolated variables.
- Legacy `YDLocal3Release`/`YDLocal4Release` and `YDLocalRelease(handle)` mean clear saved values, not release a runtime reference or destroy the owner. Generated owner callbacks use `ClearContext` on their held context; generic owner clearing uses `ClearOwner`. Shared holders immediately observe the cleared values. Handle destruction still uses `Unbind`.
- Legacy `YDLocal6*` named pages retain their intentionally shared persistent state. The adapter assigns distinct positive IDs to literal page names, then uses `NamedOwner` with ordinary typed scalar/array access. `YDLocal6Release(page)` clears that context with `ClearContext`, preserving its identity and table reference. Named-page bindings occupy `KKLC_Owners` parent key 0; non-null handle bindings occupy their nonzero native handle IDs. Named pages do not use native `StringHash(page)` identities or borrow handle bindings.
- A raw user DestroyTimer/DestroyTrigger or custom external context-ID storage bypasses lifetime tracking. Adapter must transform supported destruction sites or reject/report unsupported paths. Clear bindings before destroy. IDs may be reused only after all tracked references end; this API does not make dangling integer references valid.
- `Alive` validates present allocation, not the historical identity of a stale integer. Contexts, pages and bridge tokens are opaque and must not be cached after release/pop. Safe reuse relies on every live callback/pending/owner holding its declared reference; no generation-based stale-ID recovery is claimed.
- Allocation/refcount exhaustion reports a diagnostic and returns 0; adapter must stop that generated entry when acquisition fails. Context 0 must never be used to store data. If the engine cannot allocate all six hashtables, initialization fails closed without repeatedly creating more tables.
- Flush removes saved references; it does not destroy borrowed game objects.

## Verification boundary

Tests execute a translation of the actual JASS source with native mocks, including reference ownership, >8192 live contexts, array indices, dispatch nesting/condition failure, owner unbinding with an active callback, and a yielded body retaining its own local context. This is executable offline verification, not a Warcraft engine run.
