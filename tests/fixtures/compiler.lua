-- Original test-only compiler stub; no KKWE compiler implementation is bundled.
-- Its profile is created only inside each test's temporary tool directory.
wave = {}

function wave:do_compile(op)
    return op.fixture_exit_code or 0, "fixture stdout", "fixture stderr"
end

function wave:compile(op)
    local exit_code, out, err = self:do_compile(op)
    if exit_code ~= 0 then
        return false, out, err
    end
    return true
end
