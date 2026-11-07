// Standalone pre-Wave regression input. Transform this file with adapter.transform.
// Root may replace __REPORT_PATH__ before compiling. No map is written by this file.
// All tested business state uses the original YDLocal macro surface.
globals
    string testReportPath = "__REPORT_PATH__"
    string testBuildId = "__BUILD_ID__"
    integer testChecks = 0
    integer testFailures = 0
    string testFailureLabels = ""
    boolean testStarted = false
    boolean testSetupFinished = false
    integer testNestedDone = 0
    integer testProbeCount = 0
    integer testProbeSum = 0
    integer testRecursiveCount = 0
    integer testParameterResult = 0
    integer testWaitDone = 0
    integer testWaitSum = 0
    integer testWaitActive = 0
    integer testWaitMaxActive = 0
    integer testConditionChecks = 0
    integer testConditionUnexpected = 0
    integer testDynamicRuns = 0
    integer testDynamicSum = 0
    integer testTimerTicks = 0
    integer testArrayDone = 0
    integer testCanceledTicks = 0
    integer testEarlyDone = 0
    trigger testStartTrigger = null
    trigger testProbeTrigger = null
    trigger testNestedTrigger = null
    trigger testRecursiveTrigger = null
    trigger testParameterTrigger = null
    trigger testWaitTrigger = null
    trigger testFalseTrigger = null
    trigger testEarlyTrigger = null
    trigger testDynamicTrigger = null
endglobals

function TestAssert takes boolean passed, string label returns nothing
    set testChecks = testChecks + 1
    if not passed then
        set testFailures = testFailures + 1
        set testFailureLabels = testFailureLabels + label + ";"
        call BJDebugMsg("KKLC_TEST_FAIL " + label)
    endif
endfunction

function TestDestroyTimerExternal takes timer value returns nothing
    call DestroyTimer(value)
endfunction

function TestDestroyTriggerExternal takes trigger value returns nothing
    call DestroyTrigger(value)
endfunction

function TestProbeActions takes nothing returns nothing
    YDLocalInitialize()
    call TestAssert(YDLocal1Get(integer, "value") == 111 or YDLocal1Get(integer, "value") == 333, "probe parameter")
    set testProbeCount = testProbeCount + 1
    set testProbeSum = testProbeSum + YDLocal1Get(integer, "value")
    call YDLocal1Set(integer, "value", 999)
    call YDLocal1Release()
endfunction

function TestNestedActions takes nothing returns nothing
    local trigger ydl_trigger = testProbeTrigger
    local integer ydl_triggerstep
    YDLocalInitialize()
    call TestAssert(YDLocal1Get(integer, "value") == 6, "nested incoming parameter")
    call YDLocal1Set(integer, "value", 888)
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "value", 111)
    call YDTriggerExecuteTrigger(ydl_trigger, false)
    call TestAssert(YDLocal1Get(integer, "value") == 888, "nested caller restored")
    set testNestedDone = testNestedDone + 1
    call YDLocal1Release()
endfunction

function TestRecursiveActions takes nothing returns nothing
    local trigger ydl_trigger = testRecursiveTrigger
    local integer ydl_triggerstep
    local integer expected
    YDLocalInitialize()
    set expected = YDLocal1Get(integer, "depth")
    call TestAssert(expected >= 0 and expected <= 3, "recursive depth bounds")
    if expected > 0 then
        YDLocalExecuteTrigger(ydl_trigger)
        call YDLocal5Set(integer, "depth", expected - 1)
        call YDTriggerExecuteTrigger(ydl_trigger, false)
    endif
    call TestAssert(YDLocal1Get(integer, "depth") == expected, "same-trigger recursive frame isolation")
    set testRecursiveCount = testRecursiveCount + 1
    call YDLocal1Release()
endfunction

function TestParameterExpression takes nothing returns integer
    local trigger ydl_trigger = testProbeTrigger
    local integer ydl_triggerstep
    YDLocalInitialize()
    call YDLocal1Set(integer, "expressionValue", 42)
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "value", 333)
    call YDTriggerExecuteTrigger(ydl_trigger, false)
    return YDLocal1Get(integer, "expressionValue")
endfunction

function TestParameterActions takes nothing returns nothing
    YDLocalInitialize()
    set testParameterResult = YDLocal1Get(integer, "value")
    call TestAssert(testParameterResult == 42, "nested expression keeps outer prepared parameters")
    call YDLocal1Release()
endfunction

function TestWaitActions takes nothing returns nothing
    local integer expected
    YDLocalInitialize()
    set expected = YDLocal1Get(integer, "waitValue")
    call TestAssert(expected == 101 or expected == 202, "wait incoming parameter")
    set testWaitActive = testWaitActive + 1
    if testWaitActive > testWaitMaxActive then
        set testWaitMaxActive = testWaitActive
    endif
    if expected == 101 then
        call TriggerSleepAction(0.50)
    else
        call TriggerSleepAction(0.20)
    endif
    call YDLocalReset()
    set testWaitActive = testWaitActive - 1
    call TestAssert(YDLocal1Get(integer, "waitValue") == expected, "interleaved wait retained local context")
    set testWaitDone = testWaitDone + 1
    set testWaitSum = testWaitSum + YDLocal1Get(integer, "waitValue")
    call YDLocal1Release()
endfunction

function TestFalseCondition takes nothing returns boolean
    set testConditionChecks = testConditionChecks + 1
    return false
endfunction

function TestUnexpectedActions takes nothing returns nothing
    YDLocalInitialize()
    set testConditionUnexpected = testConditionUnexpected + 1
    call TestAssert(false, "false condition incorrectly executed action")
    call YDLocal1Release()
endfunction

function TestEarlyActions takes nothing returns nothing
    YDLocalInitialize()
    if YDLocal1Get(integer, "early") == 1 then
        set testEarlyDone = testEarlyDone + 1
        return
    endif
    call TestAssert(false, "early-return parameter")
    call YDLocal1Release()
endfunction

function TestDynamicCallback takes nothing returns boolean
    call YDLocal4Set(integer, "persist", YDLocal4Get(integer, "persist") + 1)
    set testDynamicRuns = YDLocal4Get(integer, "persist")
    set testDynamicSum = testDynamicSum + YDLocal4Get(integer, "parameter")
    call TestAssert(YDLocal4Get(integer, "parameter") == 10 or YDLocal4Get(integer, "parameter") == 20, "dynamic incoming parameter")
    return false
endfunction

function TestPeriodicTimer takes nothing returns nothing
    call YDLocal3Set(integer, "tick", YDLocal3Get(integer, "tick") + 1)
    call TestAssert(YDLocal3Get(integer, "tick") == testTimerTicks + 1, "periodic owner state persists")
    set testTimerTicks = YDLocal3Get(integer, "tick")
    if testTimerTicks >= 3 then
        call DestroyTimer(GetExpiredTimer())
        call TestAssert(YDLocal3Get(integer, "tick") == 3, "destroyed timer active callback keeps context")
        return
    endif
endfunction

function TestArrayTimer takes nothing returns nothing
    call TestAssert(YDLocal3Get(integer, "slot") == 400, "captured scalar shares array name safely")
    call TestAssert(YDLocal3ArrayGet(integer, "slot", -2147483648) == 404, "captured array INT_MIN")
    call TestAssert(YDLocal3ArrayGet(integer, "slot", -1) == 402, "captured array negative index")
    call TestAssert(YDLocal3ArrayGet(integer, "slot", 0) == 401, "captured array zero index")
    call TestAssert(YDLocal3ArrayGet(integer, "slot", 2147483647) == 403, "captured array INT_MAX")
    set testArrayDone = testArrayDone + 1
    call DestroyTimer(GetExpiredTimer())
endfunction

function TestCanceledTimer takes nothing returns nothing
    set testCanceledTicks = testCanceledTicks + 1
    call TestAssert(YDLocal3Get(integer, "canceled") == -1, "externally destroyed timer must not execute")
    call DestroyTimer(GetExpiredTimer())
endfunction

function TestSyncEnum takes nothing returns nothing
    call YDLocal2Set(integer, "bridge", YDLocal2Get(integer, "bridge") + 1)
endfunction

function TestStartActions takes nothing returns nothing
    local trigger ydl_trigger
    local timer ydl_timer
    local integer ydl_triggerstep
    local force members = CreateForce()
    YDLocalInitialize()
    set testStarted = true
    call YDLocal1Set(integer, "value", 777)
    set ydl_trigger = testNestedTrigger
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "value", 6)
    call YDTriggerExecuteTrigger(ydl_trigger, false)
    call TestAssert(YDLocal1Get(integer, "value") == 777, "ordinary caller frame isolation")

    set ydl_trigger = testRecursiveTrigger
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "depth", 3)
    call YDTriggerExecuteTrigger(ydl_trigger, false)

    set ydl_trigger = testParameterTrigger
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "value", TestParameterExpression())
    call YDTriggerExecuteTrigger(ydl_trigger, false)

    set ydl_trigger = testFalseTrigger
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "unused", 91)
    call YDTriggerExecuteTrigger(ydl_trigger, true)

    set ydl_trigger = testEarlyTrigger
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "early", 1)
    call YDTriggerExecuteTrigger(ydl_trigger, false)

    set ydl_trigger = testWaitTrigger
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "waitValue", 101)
    call YDTriggerExecuteTrigger(ydl_trigger, false)
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "waitValue", 202)
    call YDTriggerExecuteTrigger(ydl_trigger, false)

    set ydl_trigger = CreateTrigger()
    set testDynamicTrigger = ydl_trigger
    call YDLocalSet(ydl_trigger, integer, "persist", 0)
    call YDLocalSet(ydl_trigger, integer, "parameter", 0)
    call TriggerAddCondition(ydl_trigger, Condition(function TestDynamicCallback))
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "parameter", 10)
    call YDTriggerExecuteTrigger(ydl_trigger, false)
    call TestAssert(YDLocalGet(ydl_trigger, integer, "persist") == 1, "dynamic false option invokes callback")
    YDLocalExecuteTrigger(ydl_trigger)
    call YDLocal5Set(integer, "parameter", 20)
    call YDTriggerExecuteTrigger(ydl_trigger, true)
    call TestAssert(YDLocalGet(ydl_trigger, integer, "persist") == 2, "dynamic true option and persistent owner")
    call TestDestroyTriggerExternal(ydl_trigger)
    set testDynamicTrigger = null

    set ydl_timer = CreateTimer()
    call YDLocalSet(ydl_timer, integer, "tick", 0)
    call TimerStart(ydl_timer, 0.10, true, function TestPeriodicTimer)

    call YDLocal1Set(integer, "slot", 400)
    call YDLocal1ArraySet(integer, "slot", 0, 401)
    call YDLocal1ArraySet(integer, "slot", -1, 402)
    call YDLocal1ArraySet(integer, "slot", 2147483647, 403)
    call YDLocal1ArraySet(integer, "slot", -2147483648, 404)
    call TestAssert(YDLocal1Get(integer, "slot") == 400, "scalar/array namespace isolation")
    call TestAssert(YDLocal1ArrayGet(integer, "slot", -1) == 402, "root exact negative index")
    set ydl_timer = CreateTimer()
    call TimerStart(ydl_timer, 0.15, false, function TestArrayTimer)

    set ydl_timer = CreateTimer()
    call YDLocalSet(ydl_timer, integer, "canceled", 123)
    call TimerStart(ydl_timer, 0.80, false, function TestCanceledTimer)
    call TestDestroyTimerExternal(ydl_timer)

    call YDLocal1Set(integer, "bridge", 5)
    call ForceAddPlayer(members, Player(0))
    call ForForce(members, function TestSyncEnum)
    call TestAssert(YDLocal1Get(integer, "bridge") == 6, "zero-argument synchronous callback bridge")
    call DestroyForce(members)
    set testSetupFinished = true
    call YDLocal1Release()
    set members = null
    set ydl_timer = null
    set ydl_trigger = null
endfunction

function TestFinalReport takes nothing returns nothing
    call TestAssert(testStarted and testSetupFinished, "suite setup completed")
    call TestAssert(testNestedDone == 1, "nested completion")
    call TestAssert(testProbeCount == 2 and testProbeSum == 444, "nested parameter probes completed")
    call TestAssert(testRecursiveCount == 4, "finite same-trigger recursion completed")
    call TestAssert(testParameterResult == 42, "parameter-expression completion")
    call TestAssert(testWaitDone == 2 and testWaitSum == 303, "both waiting instances completed")
    call TestAssert(testWaitMaxActive == 2 and testWaitActive == 0, "waiting instances actually overlapped")
    call TestAssert(testConditionChecks == 1 and testConditionUnexpected == 0, "false condition discarded pending frame")
    call TestAssert(testEarlyDone == 1, "early return completed")
    call TestAssert(testDynamicRuns == 2 and testDynamicSum == 30, "dynamic parameters and persistent state completed")
    call TestAssert(testTimerTicks == 3, "periodic timer stopped after third tick")
    call TestAssert(testArrayDone == 1, "array capture completed")
    call TestAssert(testCanceledTicks == 0, "external timer destruction prevented callback")
    call DestroyTrigger(testStartTrigger)
    call DestroyTrigger(testProbeTrigger)
    call DestroyTrigger(testNestedTrigger)
    call DestroyTrigger(testRecursiveTrigger)
    call DestroyTrigger(testParameterTrigger)
    call DestroyTrigger(testWaitTrigger)
    call DestroyTrigger(testFalseTrigger)
    call DestroyTrigger(testEarlyTrigger)
    call PreloadGenClear()
    call PreloadGenStart()
    if testFailures == 0 then
        call BJDebugMsg("KKLC_TEST_PASS checks=" + I2S(testChecks) + " build=" + testBuildId)
        call Preload("KKLC_TEST_PASS checks=" + I2S(testChecks) + " build=" + testBuildId)
    else
        call BJDebugMsg("KKLC_TEST_FAIL failures=" + I2S(testFailures) + " checks=" + I2S(testChecks) + " build=" + testBuildId)
        call Preload("KKLC_TEST_FAIL failures=" + I2S(testFailures) + " checks=" + I2S(testChecks) + " build=" + testBuildId)
        call Preload(testFailureLabels)
    endif
    call PreloadGenEnd(testReportPath)
    call DestroyTimer(GetExpiredTimer())
endfunction

function TestLaunch takes nothing returns nothing
    call TriggerExecute(testStartTrigger)
    call DestroyTimer(GetExpiredTimer())
endfunction

function main takes nothing returns nothing
    local timer launchTimer = CreateTimer()
    local timer reportTimer = CreateTimer()
    call InitBlizzard()
    set testProbeTrigger = CreateTrigger()
    call TriggerAddAction(testProbeTrigger, function TestProbeActions)
    set testNestedTrigger = CreateTrigger()
    call TriggerAddAction(testNestedTrigger, function TestNestedActions)
    set testRecursiveTrigger = CreateTrigger()
    call TriggerAddAction(testRecursiveTrigger, function TestRecursiveActions)
    set testParameterTrigger = CreateTrigger()
    call TriggerAddAction(testParameterTrigger, function TestParameterActions)
    set testWaitTrigger = CreateTrigger()
    call TriggerAddAction(testWaitTrigger, function TestWaitActions)
    set testFalseTrigger = CreateTrigger()
    call TriggerAddCondition(testFalseTrigger, Condition(function TestFalseCondition))
    call TriggerAddAction(testFalseTrigger, function TestUnexpectedActions)
    set testEarlyTrigger = CreateTrigger()
    call TriggerAddAction(testEarlyTrigger, function TestEarlyActions)
    set testStartTrigger = CreateTrigger()
    call TriggerAddAction(testStartTrigger, function TestStartActions)
    // Report is scheduled independently, so an aborted test body cannot fake pass.
    call TimerStart(reportTimer, 3.00, false, function TestFinalReport)
    call TimerStart(launchTimer, 0.05, false, function TestLaunch)
    set launchTimer = null
    set reportTimer = null
endfunction

function config takes nothing returns nothing
    call SetMapName("Local context regression")
    call SetMapDescription("Automatic context regression, final report after three seconds")
    call SetPlayers(1)
    call SetTeams(1)
    call SetGamePlacement(MAP_PLACEMENT_USE_MAP_SETTINGS)
    call DefineStartLocation(0, 0.0, 0.0)
    call SetPlayerStartLocation(Player(0), 0)
    call SetPlayerController(Player(0), MAP_CONTROL_USER)
    call SetPlayerSlotAvailable(Player(0), MAP_CONTROL_USER)
endfunction
