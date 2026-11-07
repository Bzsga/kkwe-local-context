#include <YDTrigger/YDTrigger.h>

library First initializer Init
private function Shared takes nothing returns nothing
    call YDLocal2Set(integer, "value", 11)
endfunction
private function Init takes nothing returns nothing
    YDLocalInitialize()
    call ForGroup(null, function Shared)
    call YDLocal6Set("ui-page", integer, "value", YDLocal1Get(integer, "value"))
    call YDLocal6ArraySet("ui-page", integer, "value", 2147483647, 31)
    call YDLocal1Release()
endfunction
endlibrary

library Second initializer Init
private function Shared takes nothing returns nothing
    call YDLocal2Set(integer, "value", 22)
endfunction
private function Init takes nothing returns nothing
    YDLocalInitialize()
    call ForGroup(null, function Shared)
    call YDLocal6Set("other-page", integer, "value", YDLocal1Get(integer, "value"))
    call BJDebugMsg(I2S(YDLocal6Get("ui-page", integer, "value")))
    call YDLocal6Release("other-page")
    call YDLocal1Release()
endfunction
endlibrary

//! Check Formula
//! zinc
library ExistingZinc {
    public function Cleanup(timer value) {
        DestroyTimer(value);
    }
}
//! endzinc

function main takes nothing returns nothing
endfunction
function config takes nothing returns nothing
endfunction
