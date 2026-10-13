-- 公开配置合同及调用方配置隔离；仅计划，不操作硬件。
local root = path.absolute("..", path.absolute(os.scriptdir()))
local api = import("api", {rootdir = path.join(root, "addon/modules"), anonymous = true})
local json = import("core.base.json", {anonymous = true})
local raw_pcall = debug.global("pcall")
local passed, failed = 0, 0

local function fixture()
    return {
        host = "linux", project_root = "/tmp/project", report_dir = "/tmp/report",
        runtime = {python = {program = "uv", args = {"run", "--offline", "--no-project", "python"}}},
        chip = "GD32F427", lock_namespace = "gd32",
        debug = {probe = "ABC", frequency = 100000, protocol = "swd", connect = "halt"},
        pyocd = {target_override = "gd32f427", ["cache.enable_memory"] = false, ["cache.read_code_from_elf"] = false},
        artifacts = {elf = "/tmp/program.elf"}, memory = {flash = {start = 0x08000000, size = 1024}},
        flash = {verify = true, frequencies = {4000000, 100000}},
        build = {target = "firmware", jobs = 8}
    }
end

local function test(name, callback)
    local ok, errors = raw_pcall(callback)
    if ok then passed = passed + 1; print("[PASS] %s", name)
    else failed = failed + 1; print("[FAIL] %s: %s", name, errors) end
end

test("配置计划统一频率与协议", function()
    local result = api.plan(fixture(), "flash")
    assert(result.pyocd.frequency == 100000 and result.pyocd.dap_protocol == "swd")
end)
test("计划及返回对象修改不污染调用方 config", function()
    local original = fixture()
    local result = api.plan(original, "flash")
    assert(original.pyocd.frequency == nil and original.pyocd.dap_protocol == nil, "plan 修改了原 config.pyocd")
    result.artifacts.elf = "/modified"
    result.flash.frequencies[1] = 123
    result.runtime.python.args[1] = "changed"
    assert(original.artifacts.elf == "/tmp/program.elf")
    assert(original.flash.frequencies[1] == 4000000)
    assert(original.runtime.python.args[1] == "run")
end)
test("拒绝冲突频率及协议", function()
    for _, field in ipairs({"frequency", "dap_protocol"}) do
        local original = fixture()
        original.pyocd[field] = field == "frequency" and 1 or "jtag"
        assert(not raw_pcall(function() api.plan(original, "flash") end))
    end
end)
test("拒绝缺少关键参数和错误参数值", function()
    local cases = {
        function(c) c.host = "auto" end,
        function(c) c.project_root = nil end,
        function(c) c.report_dir = nil end,
        function(c) c.runtime.python = nil end,
        function(c) c.runtime.python.args = "--invalid" end,
        function(c) c.runtime.python.args = {true} end,
        function(c) c.runtime.python.args = {[1] = "run", [3] = "python"} end,
        function(c) c.runtime.python.args = {[1] = "run", [3] = "--offline", [4] = "python"} end,
        function(c) c.runtime.python.args = {[1] = "run", [2] = "--offline", [4] = "python"} end,
        function(c) c.runtime.python.args = {option = "run"} end,
        function(c) c.chip = nil end,
        function(c) c.lock_namespace = "../escape" end,
        function(c) c.debug.probe = nil end,
        function(c) c.debug.frequency = 0 end,
        function(c) c.debug.frequency = 1.5 end,
        function(c) c.debug.protocol = "invalid" end,
        function(c) c.debug.connect = "invalid" end,
        function(c) c.pyocd.target_override = nil end,
        function(c) c.artifacts.elf = nil end,
        function(c) c.memory.flash.start = -1 end,
        function(c) c.memory.flash.size = 0 end,
        function(c) c.flash.verify = nil end,
        function(c) c.flash.frequencies = {} end,
        function(c) c.flash.frequencies = {0} end,
        function(c) c.flash.frequencies = {100000, -1} end,
        function(c) c.flash.frequencies = {100000, 1.5} end,
        function(c) c.flash.frequencies = {"100000"} end,
        function(c) c.flash.frequencies = {frequency = 100000} end,
        function(c) c.flash.frequencies = {[0] = 100000, [1] = 100000} end,
        function(c) c.flash.frequencies = {[1] = 4000000, [3] = 100000} end,
        function(c) c.flash.frequencies = {[1] = 4000000, [3] = 200000, [4] = 100000} end,
        function(c) c.flash.frequencies = {[1] = 4000000, [2] = 200000, [4] = 100000} end,
        function(c) c.pyocd["cache.enable_memory"] = true end,
        function(c) c.pyocd["cache.read_code_from_elf"] = true end,
        function(c) c.build.jobs = 0 end,
        function(c) c.build.target = nil end,
    }
    for index, modify in ipairs(cases) do
        local c = fixture(); modify(c)
        assert(not raw_pcall(function() api.plan(c, "flash") end), "非法参数被接受：" .. index)
    end
end)
test("probes 不要求芯片或下载参数", function()
    local c = fixture()
    c.chip = nil; c.debug = nil; c.pyocd = nil; c.artifacts = nil; c.memory = nil; c.flash = nil; c.build = nil
    assert(api.plan(c, "probes").host == "linux")
end)
test("reset 不要求下载参数但拒绝 build", function()
    local c = fixture(); c.artifacts = nil; c.memory = nil; c.flash = nil
    assert(not raw_pcall(function() api.plan(c, "reset") end))
    c.build = nil
    assert(api.plan(c, "reset").chip == "GD32F427")
end)
test("Windows 必须显式提供执行工具", function()
    local c = fixture(); c.host = "windows"
    assert(not raw_pcall(function() api.plan(c, "flash") end))
    c.runtime.powershell = "powershell.exe"; c.runtime.wslpath = "wslpath"
    assert(api.plan(c, "flash").host == "windows")
    c.runtime.python = {uv = "uv.exe", tool = "pyocd"}
    assert(api.plan(c, "flash").runtime.python.tool == "pyocd")
end)
test("未知操作及 config 类型明确拒绝", function()
    assert(not raw_pcall(function() api.plan({}, "erase") end))
    assert(not raw_pcall(function() api.plan("config", "flash") end))
end)

local function with_execution_stubs(callback)
    local original_execv, original_iorunv = os.execv, os.iorunv
    local calls = {}
    os.execv = function(program, args, options)
        local call = {program = program, args = args, options = options}
        calls[#calls + 1] = call
        for index, value in ipairs(args) do
            if value == "--config" then call.config = json.loadfile(args[index + 1]) end
            if value == "-Config" then
                local file = args[index + 1]:sub(#"windows:" + 1)
                call.config = json.loadfile(file)
                call.directory = path.directory(file)
            end
        end
    end
    os.iorunv = function(program, args)
        assert(program == "wslpath" and args[1] == "-w")
        return "windows:" .. args[2] .. "\n"
    end
    local ok, errors = raw_pcall(function() callback(calls) end)
    os.execv, os.iorunv = original_execv, original_iorunv
    assert(ok, errors)
end

test("Linux run 先构建并向显式解释器传递独立 config", function()
    local directory = os.tmpfile() .. "-pyocd-run"
    local c = fixture(); c.project_root = directory; c.report_dir = path.join(directory, "reports")
    with_execution_stubs(function(calls)
        api.flash(c)
        assert(#calls == 2)
        assert(calls[1].program == os.programfile())
        assert(table.concat(calls[1].args, " ") == "build -j 8 firmware")
        assert(calls[1].options.curdir == directory)
        assert(calls[2].program == "uv" and calls[2].args[1] == "run")
        assert(calls[2].args[#calls[2].args] == "flash")
        assert(calls[2].config.pyocd.frequency == c.debug.frequency)
        assert(c.pyocd.frequency == nil)
    end)
    os.tryrm(directory)
end)
test("Windows run 转换副本路径且始终释放运行配置", function()
    local directory = os.tmpfile() .. "-pyocd-windows"
    local c = fixture(); c.host = "windows"; c.build = nil
    c.project_root = directory; c.report_dir = path.join(directory, "reports")
    c.runtime.powershell = "powershell.exe"; c.runtime.wslpath = "wslpath"
    c.pyocd.pack = {"/tmp/device.pack"}; c.pyocd.user_script = "/tmp/target.py"
    with_execution_stubs(function(calls)
        api.flash(c)
        assert(#calls == 1 and calls[1].program == "powershell.exe")
        local native = calls[1].config
        assert(native.project_root == "windows:" .. directory)
        assert(native.artifacts.elf == "windows:/tmp/program.elf")
        assert(native.pyocd.pack[1] == "windows:/tmp/device.pack")
        assert(native.pyocd.user_script == "windows:/tmp/target.py")
        assert(c.artifacts.elf == "/tmp/program.elf" and c.pyocd.pack[1] == "/tmp/device.pack")
        assert(c.pyocd.user_script == "/tmp/target.py" and c.pyocd.frequency == nil)
        assert(not os.exists(calls[1].directory), "未释放临时运行配置")
    end)
    os.tryrm(directory)
end)
test("后端异常向调用方传播并释放运行配置", function()
    local directory = os.tmpfile() .. "-pyocd-failure"
    local c = fixture(); c.build = nil; c.project_root = directory; c.report_dir = path.join(directory, "reports")
    local original_execv = os.execv
    local runtime_directory
    os.execv = function(_, args)
        for index, value in ipairs(args) do
            if value == "--config" then runtime_directory = path.directory(args[index + 1]) end
        end
        raise("测试后端执行失败")
    end
    local ok, errors = raw_pcall(function() api.flash(c) end)
    os.execv = original_execv
    local cleaned = runtime_directory and not os.exists(runtime_directory)
    os.tryrm(directory)
    assert(not ok and tostring(errors):find("测试后端执行失败", 1, true), tostring(errors))
    assert(cleaned, "异常路径未清理运行配置")
    assert(c.pyocd.frequency == nil)
end)

print("x-pyocd API: %d 通过，%d 失败", passed, failed)
assert(failed == 0, "x-pyocd API 测试失败")
