// Synthetic pre-Wave KKWE template, constructed from the inspected generator.
// It is not a map and is safe for standalone compiler validation.
globals
    trigger gg_trg_KKLC_Demo
endglobals

function KKDemo_Group takes nothing returns nothing
    call YDLocal2Set(integer, "count", YDLocal2Get(integer, "count") + 1)
endfunction

function KKDemo_Timer takes nothing returns nothing
    call BJDebugMsg(I2S(YDLocalArrayGet(GetExpiredTimer(), integer, "a", 1)))
    call YDLocalSet(GetExpiredTimer(), unit, "unit", null)
    call DestroyTimer(GetExpiredTimer())
endfunction

function KKDemo_Dynamic takes nothing returns boolean
    call YDLocal4Set(integer, "count", YDLocal4Get(integer, "count") + 1)
    return false
endfunction

function KKDemo_Child_Actions takes nothing returns nothing
    YDLocalInitialize()
    call BJDebugMsg(I2S(YDLocal1Get(integer, "value")))
    call TriggerSleepAction(.01)
    call YDLocalReset()
    call YDLocal1Release()
endfunction

function KKDemo_Main_Actions takes nothing returns nothing
    local timer ydl_timer
    local trigger ydl_trigger
    local integer ydl_triggerstep
    local group g = CreateGroup()
    YDLocalInitialize()
    call YDLocal1Set(unitcode, "value", 42)
    call YDLocal1Set(integer, "count", 0)
    call YDLocal1ArraySet(integer, "a", 1, 777)
    call YDLocal1ArraySet(integer, "a", -2147483648, 888)
    call ForGroup(g, function KKDemo_Group)
    set ydl_timer = CreateTimer()
    call YDLocalArraySet(ydl_timer, integer, "a", 0, 123)
    call TimerStart(ydl_timer, .01, false, function KKDemo_Timer)
    set ydl_trigger = CreateTrigger()
    call YDLocalSet(ydl_trigger, integer, "count", 0)
    call TriggerAddCondition(ydl_trigger, Condition(function KKDemo_Dynamic))
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "count", 11)
    call YDTriggerExecuteTrigger(ydl_trigger, false)
    call DestroyTrigger(ydl_trigger)
    set ydl_trigger = gg_trg_KKLC_Demo
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "value", YDLocal1Get(integer, "value"))
    call YDTriggerExecuteTrigger(ydl_trigger, true)
    call DestroyGroup(g)
    set g = null
    set ydl_timer = null
    set ydl_trigger = null
    call YDLocal1Release()
endfunction

function main takes nothing returns nothing
    set gg_trg_KKLC_Demo = CreateTrigger()
    call TriggerAddAction(gg_trg_KKLC_Demo, function KKDemo_Child_Actions)
    call KKDemo_Main_Actions()
endfunction

function config takes nothing returns nothing
endfunction
