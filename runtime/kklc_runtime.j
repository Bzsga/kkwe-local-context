// KKLC runtime v1. Generated callers own every returned context reference.
// Metadata keys: 0 kind, 1 refs/free-next, 2 list-next/head, 3 index-head/value/ctx.
// Kinds: 1 context, 2 array page, 3 index node, 4 synchronous bridge.
globals
    hashtable KKLC_Data = null
    hashtable KKLC_Arrays = null
    hashtable KKLC_Meta = null
    hashtable KKLC_ArrayMap = null
    hashtable KKLC_IndexMap = null
    hashtable KKLC_Owners = null
    boolean KKLC_InitAttempted = false
    boolean KKLC_Ready = false
    integer KKLC_NextId = 0
    integer KKLC_FreeHead = 0
    integer KKLC_BridgeHead = 0
    integer KKLC_PendingCtx = 0
    trigger KKLC_PendingTarget = null
    integer KKLC_Errors = 0
endglobals

function KKLC_Init takes nothing returns nothing
    if not KKLC_InitAttempted then
        set KKLC_InitAttempted = true
        set KKLC_Data = InitHashtable()
        set KKLC_Arrays = InitHashtable()
        set KKLC_Meta = InitHashtable()
        set KKLC_ArrayMap = InitHashtable()
        set KKLC_IndexMap = InitHashtable()
        set KKLC_Owners = InitHashtable()
        set KKLC_Ready = KKLC_Data != null and KKLC_Arrays != null and KKLC_Meta != null and KKLC_ArrayMap != null and KKLC_IndexMap != null and KKLC_Owners != null
        if not KKLC_Ready then
            set KKLC_Errors = KKLC_Errors + 1
            call BJDebugMsg("KKLC: cannot allocate six runtime hashtables")
        endif
    endif
endfunction

function KKLC_Fail takes string message returns nothing
    set KKLC_Errors = KKLC_Errors + 1
    call BJDebugMsg("KKLC: " + message)
endfunction

function KKLC_AllocNode takes integer kind returns integer
    local integer id
    call KKLC_Init()
    if not KKLC_Ready then
        return 0
    endif
    if KKLC_FreeHead != 0 then
        set id = KKLC_FreeHead
        set KKLC_FreeHead = LoadInteger(KKLC_Meta, id, 1)
        call FlushChildHashtable(KKLC_Meta, id)
    else
        if KKLC_NextId >= 2147483647 then
            call KKLC_Fail("logical ID space exhausted")
            return 0
        endif
        set KKLC_NextId = KKLC_NextId + 1
        set id = KKLC_NextId
    endif
    call SaveInteger(KKLC_Meta, id, 0, kind)
    return id
endfunction

function KKLC_FreeNode takes integer id returns nothing
    call FlushChildHashtable(KKLC_Meta, id)
    call SaveInteger(KKLC_Meta, id, 1, KKLC_FreeHead)
    set KKLC_FreeHead = id
endfunction

function KKLC_Alive takes integer ctx returns boolean
    call KKLC_Init()
    return KKLC_Ready and ctx > 0 and LoadInteger(KKLC_Meta, ctx, 0) == 1
endfunction

function KKLC_Alloc takes nothing returns integer
    local integer ctx = KKLC_AllocNode(1)
    if ctx != 0 then
        call SaveInteger(KKLC_Meta, ctx, 1, 1)
    endif
    return ctx
endfunction

function KKLC_Retain takes integer ctx returns integer
    local integer refs
    if not KKLC_Alive(ctx) then
        if ctx != 0 then
            call KKLC_Fail("retain of inactive context")
        endif
        return 0
    endif
    set refs = LoadInteger(KKLC_Meta, ctx, 1)
    if refs >= 2147483647 then
        call KKLC_Fail("reference count exhausted")
        return 0
    endif
    call SaveInteger(KKLC_Meta, ctx, 1, refs + 1)
    return ctx
endfunction

function KKLC_ClearContext takes integer ctx returns nothing
    local integer page
    local integer nextPage
    local integer node
    local integer nextNode
    if not KKLC_Alive(ctx) then
        return
    endif
    set page = LoadInteger(KKLC_Meta, ctx, 2)
    loop
        exitwhen page == 0
        set nextPage = LoadInteger(KKLC_Meta, page, 2)
        set node = LoadInteger(KKLC_Meta, page, 3)
        loop
            exitwhen node == 0
            set nextNode = LoadInteger(KKLC_Meta, node, 2)
            call KKLC_FreeNode(node)
            set node = nextNode
        endloop
        call FlushChildHashtable(KKLC_Arrays, page)
        call FlushChildHashtable(KKLC_IndexMap, page)
        call KKLC_FreeNode(page)
        set page = nextPage
    endloop
    call FlushChildHashtable(KKLC_ArrayMap, ctx)
    call FlushChildHashtable(KKLC_Data, ctx)
    call SaveInteger(KKLC_Meta, ctx, 2, 0)
endfunction

function KKLC_Release takes integer ctx returns nothing
    local integer refs
    if ctx == 0 then
        return
    endif
    if not KKLC_Alive(ctx) then
        call KKLC_Fail("release of inactive context")
        return
    endif
    set refs = LoadInteger(KKLC_Meta, ctx, 1) - 1
    if refs > 0 then
        call SaveInteger(KKLC_Meta, ctx, 1, refs)
        return
    endif
    call KKLC_ClearContext(ctx)
    call KKLC_FreeNode(ctx)
endfunction

function KKLC_Array takes integer ctx, integer symbol, boolean create returns integer
    local integer page
    if not KKLC_Alive(ctx) then
        return 0
    endif
    set page = LoadInteger(KKLC_ArrayMap, ctx, symbol)
    if page == 0 and create then
        set page = KKLC_AllocNode(2)
        if page != 0 then
            call SaveInteger(KKLC_Meta, page, 2, LoadInteger(KKLC_Meta, ctx, 2))
            call SaveInteger(KKLC_Meta, ctx, 2, page)
            call SaveInteger(KKLC_ArrayMap, ctx, symbol, page)
        endif
    endif
    return page
endfunction

function KKLC_Touch takes integer page, integer index returns boolean
    local integer node
    if page == 0 or LoadInteger(KKLC_Meta, page, 0) != 2 then
        call KKLC_Fail("array write without an active page")
        return false
    endif
    if HaveSavedInteger(KKLC_IndexMap, page, index) then
        return true
    endif
    set node = KKLC_AllocNode(3)
    if node == 0 then
        return false
    endif
    call SaveInteger(KKLC_Meta, node, 2, LoadInteger(KKLC_Meta, page, 3))
    call SaveInteger(KKLC_Meta, node, 3, index)
    call SaveInteger(KKLC_Meta, page, 3, node)
    call SaveInteger(KKLC_IndexMap, page, index, node)
    return true
endfunction

function KKLC_IndexFirst takes integer page returns integer
    if page == 0 or LoadInteger(KKLC_Meta, page, 0) != 2 then
        return 0
    endif
    return LoadInteger(KKLC_Meta, page, 3)
endfunction

function KKLC_IndexNext takes integer node returns integer
    if node == 0 or LoadInteger(KKLC_Meta, node, 0) != 3 then
        return 0
    endif
    return LoadInteger(KKLC_Meta, node, 2)
endfunction

function KKLC_IndexValue takes integer node returns integer
    if node == 0 or LoadInteger(KKLC_Meta, node, 0) != 3 then
        return 0
    endif
    return LoadInteger(KKLC_Meta, node, 3)
endfunction

function KKLC_ClearArray takes integer ctx, integer symbol returns nothing
    local integer page
    local integer cursor
    local integer previous = 0
    local integer node
    local integer nextNode
    if not KKLC_Alive(ctx) then
        return
    endif
    set page = LoadInteger(KKLC_ArrayMap, ctx, symbol)
    if page == 0 then
        return
    endif
    set cursor = LoadInteger(KKLC_Meta, ctx, 2)
    loop
        exitwhen cursor == 0 or cursor == page
        set previous = cursor
        set cursor = LoadInteger(KKLC_Meta, cursor, 2)
    endloop
    if cursor == 0 then
        call KKLC_Fail("array page missing from context ownership list")
        return
    endif
    if previous == 0 then
        call SaveInteger(KKLC_Meta, ctx, 2, LoadInteger(KKLC_Meta, page, 2))
    else
        call SaveInteger(KKLC_Meta, previous, 2, LoadInteger(KKLC_Meta, page, 2))
    endif
    set node = LoadInteger(KKLC_Meta, page, 3)
    loop
        exitwhen node == 0
        set nextNode = LoadInteger(KKLC_Meta, node, 2)
        call KKLC_FreeNode(node)
        set node = nextNode
    endloop
    call FlushChildHashtable(KKLC_Arrays, page)
    call FlushChildHashtable(KKLC_IndexMap, page)
    call RemoveSavedInteger(KKLC_ArrayMap, ctx, symbol)
    call KKLC_FreeNode(page)
endfunction

// Named pages share persistent state by a collision-free compile-time page ID.
// Parent 0 is reserved here; non-null native handles use their own nonzero IDs.
function KKLC_NamedOwner takes integer page returns integer
    local integer ctx
    call KKLC_Init()
    if not KKLC_Ready or page <= 0 then
        return 0
    endif
    set ctx = LoadInteger(KKLC_Owners, 0, page)
    if ctx == 0 then
        set ctx = KKLC_Alloc()
        if ctx != 0 then
            call SaveInteger(KKLC_Owners, 0, page, ctx)
        endif
    endif
    return ctx
endfunction

function KKLC_Owner takes handle object returns integer
    local integer ctx
    local integer key
    call KKLC_Init()
    if not KKLC_Ready or object == null then
        return 0
    endif
    set key = GetHandleId(object)
    set ctx = LoadInteger(KKLC_Owners, key, 0)
    if ctx == 0 then
        set ctx = KKLC_Alloc()
        if ctx != 0 then
            call SaveInteger(KKLC_Owners, key, 0, ctx)
        endif
    endif
    return ctx
endfunction

function KKLC_AcquireOwner takes handle object returns integer
    return KKLC_Retain(KKLC_Owner(object))
endfunction

function KKLC_ClearOwner takes handle object returns nothing
    call KKLC_Init()
    if not KKLC_Ready or object == null then
        return
    endif
    call KKLC_ClearContext(LoadInteger(KKLC_Owners, GetHandleId(object), 0))
endfunction

function KKLC_Unbind takes handle object returns nothing
    local integer ctx
    local integer key
    call KKLC_Init()
    if not KKLC_Ready or object == null then
        return
    endif
    set key = GetHandleId(object)
    set ctx = LoadInteger(KKLC_Owners, key, 0)
    call FlushChildHashtable(KKLC_Owners, key)
    call KKLC_Release(ctx)
endfunction

function KKLC_MarkDynamic takes trigger target returns nothing
    call KKLC_Init()
    if KKLC_Ready and target != null then
        call SaveBoolean(KKLC_Owners, GetHandleId(target), 1, true)
    endif
endfunction

function KKLC_IsDynamic takes trigger target returns boolean
    call KKLC_Init()
    return KKLC_Ready and target != null and LoadBoolean(KKLC_Owners, GetHandleId(target), 1)
endfunction

function KKLC_Prepare takes trigger target returns integer
    if target == null then
        return 0
    endif
    if KKLC_IsDynamic(target) then
        return KKLC_AcquireOwner(target)
    endif
    return KKLC_Alloc()
endfunction

function KKLC_EnterRoot takes trigger target returns integer
    local integer ctx
    if target != null and target == KKLC_PendingTarget and KKLC_PendingCtx != 0 then
        set ctx = KKLC_PendingCtx
        set KKLC_PendingCtx = 0
        set KKLC_PendingTarget = null
        return ctx
    endif
    return KKLC_Alloc()
endfunction

function KKLC_Execute takes trigger target, boolean check, integer ctx returns nothing
    local boolean run = true
    local integer previousCtx
    local trigger previousTarget
    if not KKLC_Alive(ctx) then
        call KKLC_Fail("dispatch without an active prepared context")
        return
    endif
    if target == null then
        call KKLC_Release(ctx)
        return
    endif
    if KKLC_IsDynamic(target) then
        // Generated dynamic body is a Condition callback, not a trigger action.
        // It acquires the persistent owner and releases its own callback reference.
        call TriggerEvaluate(target)
        call KKLC_Release(ctx)
        return
    endif
    if check then
        set run = TriggerEvaluate(target)
    endif
    if not run then
        call KKLC_Release(ctx)
        return
    endif
    set previousCtx = KKLC_PendingCtx
    set previousTarget = KKLC_PendingTarget
    set KKLC_PendingCtx = ctx
    set KKLC_PendingTarget = target
    call TriggerExecute(target)
    if KKLC_PendingCtx == ctx and KKLC_PendingTarget == target then
        // No generated entry accepted ownership (e.g. no action).
        call KKLC_Release(ctx)
    endif
    set KKLC_PendingCtx = previousCtx
    set KKLC_PendingTarget = previousTarget
    set previousTarget = null
endfunction

function KKLC_BridgePush takes integer ctx returns integer
    local integer token
    local integer held = KKLC_Retain(ctx)
    if held == 0 then
        return 0
    endif
    set token = KKLC_AllocNode(4)
    if token == 0 then
        call KKLC_Release(held)
        return 0
    endif
    call SaveInteger(KKLC_Meta, token, 2, KKLC_BridgeHead)
    call SaveInteger(KKLC_Meta, token, 3, ctx)
    set KKLC_BridgeHead = token
    return token
endfunction

function KKLC_BridgeAcquire takes nothing returns integer
    if KKLC_BridgeHead == 0 then
        call KKLC_Fail("callback without a synchronous context bridge")
        return 0
    endif
    return KKLC_Retain(LoadInteger(KKLC_Meta, KKLC_BridgeHead, 3))
endfunction

function KKLC_BridgePop takes integer token returns nothing
    local integer ctx
    if token == 0 or token != KKLC_BridgeHead then
        call KKLC_Fail("out-of-order synchronous context bridge pop")
        return
    endif
    set ctx = LoadInteger(KKLC_Meta, token, 3)
    set KKLC_BridgeHead = LoadInteger(KKLC_Meta, token, 2)
    call KKLC_FreeNode(token)
    call KKLC_Release(ctx)
endfunction
