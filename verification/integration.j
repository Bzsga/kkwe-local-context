globals
    trigger testNormal = null
    trigger testDynamic = null
    integer testResult = 0
endglobals

function TestEnum takes nothing returns nothing
    call YDLocal2Set(integer, "count", YDLocal2Get(integer, "count") + 1)
endfunction

function TestFilter takes nothing returns boolean
    return YDLocal2Get(integer, "count") >= 0
endfunction

function TestTimer takes nothing returns nothing
    call YDLocal3Set(integer, "count", YDLocal3Get(integer, "count") + 1)
    set testResult = YDLocal3ArrayGet(integer, "count", -1)
    call YDLocal3Release()
    call DestroyTimer(GetExpiredTimer())
endfunction

function TestDynamic takes nothing returns boolean
    call YDLocal4Set(integer, "persist", YDLocal4Get(integer, "persist") + 1)
    return true
endfunction

function TestReceiver takes nothing returns nothing
    YDLocalInitialize()
    set testResult = YDLocal1Get(integer, "parameter")
    call TriggerSleepAction(0.01)
    set testResult = YDLocal1Get(integer, "parameter")
    call YDLocal1Release()
endfunction

function TestRoot takes nothing returns nothing
    local timer ydl_timer
    local trigger ydl_trigger = null
    local integer ydl_triggerstep
    local group localGroup = CreateGroup()
    YDLocalInitialize()
    set ydl_timer = CreateTimer()
    call YDLocal1Set(integer, "count", 10)
    call YDLocal1ArraySet(integer, "count", -1, 42)
    call YDLocal1Set(unit, "actor", GetTriggerUnit())
    call YDLocal1Set(unit, "actor", null)
    call YDLocal1Set(real, "angle", 90.0)
    call YDLocal1Set(string, "text", "中文值")
    call ForGroup(localGroup, function TestEnum)
    call GroupEnumUnitsInRange(localGroup, 0.0, 0.0, 100.0, Filter(function TestFilter))
    call YDLocalSet(ydl_timer, integer, "count", 2)
    call TimerStart(ydl_timer, 0.01, false, function TestTimer)
    set ydl_trigger = CreateTrigger()
    set testDynamic = ydl_trigger
    call YDLocalSet(ydl_trigger, integer, "persist", 7)
    call TriggerAddCondition(ydl_trigger, Condition(function TestDynamic))
    set ydl_trigger = testNormal
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "parameter", 77)
    call YDTriggerExecuteTrigger(ydl_trigger, true)
    call DestroyGroup(localGroup)
    call YDLocal1Release()
    set localGroup = null
    set ydl_trigger = null
    set ydl_timer = null
endfunction

function TestDestroyExternal takes timer t, trigger trg returns nothing
    call DestroyTimer(t)
    call DestroyTrigger(trg)
endfunction

function main takes nothing returns nothing
    set testNormal = CreateTrigger()
    call TriggerAddAction(testNormal, function TestReceiver)
endfunction

function config takes nothing returns nothing
endfunction
