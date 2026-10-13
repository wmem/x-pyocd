-- 公开配置合同；不读取板描述，也不猜测芯片、探针或执行系统。
import("core.base.json")
local resources = path.join(os.scriptdir(), "runtime")

local function copy(value)
    if type(value) ~= "table" then
        return value
    end
    local result = {}
    for key, item in pairs(value) do
        result[key] = copy(item)
    end
    return result
end

local function text(value, field)
    assert(type(value) == "string" and #value > 0, "x-pyocd: 请配置 " .. field)
    return value
end

local function positive(value, field)
    assert(
        type(value) == "number" and value > 0 and value % 1 == 0,
        "x-pyocd: " .. field .. " 必须为正整数"
    )
end

local function arguments(value)
    if value == nil then
        return
    end
    assert(type(value) == "table", "x-pyocd: python.args 必须为字符串数组")
    for key, item in pairs(value) do
        assert(
            type(key) == "number"
                and key >= 1
                and key <= #value
                and key % 1 == 0
                and type(item) == "string",
            "x-pyocd: python.args 必须为连续字符串数组"
        )
    end
end

function plan(config, operation)
    assert(type(config) == "table", "x-pyocd: config 必须为 table")
    assert(
        operation == "flash"
            or operation == "reset"
            or operation == "probes"
            or operation == "readback",
        "x-pyocd: 未知操作"
    )
    local c = copy(config)
    assert(c.host == "linux" or c.host == "windows", "x-pyocd: host 必须为 linux/windows")
    text(c.project_root, "project_root")
    text(c.report_dir, "report_dir")
    assert(type(c.runtime) == "table", "x-pyocd: 请配置 runtime")
    local python = c.runtime.python
    if type(python) == "table" and c.host == "windows" and python.tool then
        text(python.uv, "runtime.python.uv")
        text(python.tool, "runtime.python.tool")
    elseif type(python) == "table" then
        text(python.program, "runtime.python.program")
        arguments(python.args)
    else
        text(python, "runtime.python")
    end
    if c.host == "windows" then
        text(c.runtime.powershell, "runtime.powershell")
        if os.host() ~= "windows" then
            text(c.runtime.wslpath, "runtime.wslpath")
        end
    else
        assert(os.host() == "linux", "x-pyocd: Linux 后端需要 Linux 执行环境")
    end
    if operation ~= "probes" then
        text(c.chip, "chip")
        text(c.lock_namespace, "lock_namespace")
        assert(c.lock_namespace:match("^[%w_-]+$"), "x-pyocd: lock_namespace 含非法字符")
        assert(type(c.debug) == "table", "x-pyocd: 请配置 debug")
        text(c.debug.probe, "debug.probe")
        positive(c.debug.frequency, "debug.frequency")
        assert(
            c.debug.protocol == "swd" or c.debug.protocol == "jtag",
            "x-pyocd: 非法 debug.protocol"
        )
        assert(
            table.contains({ "attach", "halt", "pre-reset", "under-reset" }, c.debug.connect),
            "x-pyocd: 非法 debug.connect"
        )
        assert(type(c.pyocd) == "table", "x-pyocd: 请配置 pyocd")
        text(c.pyocd.target_override, "pyocd.target_override")
        assert(
            c.pyocd.frequency == nil or c.pyocd.frequency == c.debug.frequency,
            "x-pyocd: pyocd.frequency 与 debug.frequency 冲突"
        )
        assert(
            c.pyocd.dap_protocol == nil or c.pyocd.dap_protocol == c.debug.protocol,
            "x-pyocd: pyocd.dap_protocol 与 debug.protocol 冲突"
        )
        c.pyocd.frequency = c.debug.frequency
        c.pyocd.dap_protocol = c.debug.protocol
    end
    if operation == "flash" or operation == "readback" then
        assert(type(c.artifacts) == "table", "x-pyocd: 请配置 artifacts")
        text(c.artifacts.elf, "artifacts.elf")
        assert(
            type(c.memory) == "table" and type(c.memory.flash) == "table",
            "x-pyocd: 请配置 memory.flash"
        )
        local flash = c.memory.flash
        assert(
            type(flash.start) == "number" and flash.start >= 0 and flash.start % 1 == 0,
            "x-pyocd: memory.flash.start 非法"
        )
        positive(flash.size, "memory.flash.size")
    end
    if operation == "readback" then
        assert(c.debug.connect == "attach", "x-pyocd: readback 必须使用 attach")
    end
    if operation == "flash" then
        assert(
            type(c.flash) == "table" and type(c.flash.verify) == "boolean",
            "x-pyocd: 请配置 flash.verify"
        )
        assert(
            type(c.flash.frequencies) == "table" and #c.flash.frequencies > 0,
            "x-pyocd: 请配置 flash.frequencies"
        )
        for key, speed in pairs(c.flash.frequencies) do
            assert(
                type(key) == "number" and key >= 1 and key <= #c.flash.frequencies and key % 1 == 0,
                "x-pyocd: flash.frequencies 必须为连续数组"
            )
            positive(speed, "flash.frequencies")
        end
    end
    if operation == "readback" or (operation == "flash" and c.flash.verify) then
        assert(
            c.pyocd["cache.enable_memory"] == false and c.pyocd["cache.read_code_from_elf"] == false,
            "x-pyocd: 实读校验要求显式关闭 cache.enable_memory/cache.read_code_from_elf"
        )
    end
    if c.build ~= nil then
        assert(operation == "flash" and type(c.build) == "table", "x-pyocd: build 仅用于 flash")
        text(c.build.target, "build.target")
        positive(c.build.jobs, "build.jobs")
    end
    return c
end

local function windows_path(c, file)
    if os.host() == "windows" then
        return file
    end
    return os.iorunv(c.runtime.wslpath, { "-w", file }):trim()
end

local function native_config(c)
    for _, field in ipairs({ "project_root", "report_dir" }) do
        c[field] = windows_path(c, c[field])
    end
    if c.artifacts then
        for key, file in pairs(c.artifacts) do
            c.artifacts[key] = windows_path(c, file)
        end
    end
    if c.pyocd then
        if c.pyocd.pack then
            for i, file in ipairs(c.pyocd.pack) do
                c.pyocd.pack[i] = windows_path(c, file)
            end
        end
        if c.pyocd.user_script then
            c.pyocd.user_script = windows_path(c, c.pyocd.user_script)
        end
    end
    return c
end

function run(config, operation)
    local c = plan(config, operation)
    if c.build then
        os.execv(
            os.programfile(),
            { "build", "-j", tostring(c.build.jobs), c.build.target },
            { curdir = c.project_root }
        )
    end
    local directory = path.join(c.report_dir, "x-pyocd-runtime-" .. path.filename(os.tmpfile()))
    os.mkdir(directory)
    try({
        function()
            local file = path.join(directory, "config.json")
            if c.host == "windows" then
                local native = native_config(copy(c))
                json.savefile(file, native, { indent = true })
                os.execv(
                    c.runtime.powershell,
                    {
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        windows_path(c, path.join(resources, "run.ps1")),
                        "-Script",
                        windows_path(c, path.join(resources, "worker.py")),
                        "-Config",
                        windows_path(c, file),
                        "-Operation",
                        operation,
                    }
                )
            else
                json.savefile(file, c, { indent = true })
                local python = c.runtime.python
                local program = type(python) == "table" and python.program or python
                local args = type(python) == "table" and table.clone(python.args or {}) or {}
                table.join2(
                    args,
                    { path.join(resources, "worker.py"), "--config", file, operation }
                )
                os.execv(program, args, { curdir = c.project_root })
            end
        end,
        finally({
            function(ok, errors)
                os.tryrm(directory)
                if not ok then
                    raise(errors)
                end
            end,
        }),
    })
end

function flash(config)
    return run(config, "flash")
end
function reset(config)
    return run(config, "reset")
end
function probes(config)
    return run(config, "probes")
end
function readback(config)
    return run(config, "readback")
end

-- 应用测试桥接可复用同一 Python 后端；路径按当前安装位置解析。
function backend()
    return path.join(resources, "worker.py")
end
