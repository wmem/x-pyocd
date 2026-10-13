# x-pyocd 0.1.0

x-pyocd 是 Xmake Addon，为工程提供 pyOCD 探针枚举、ELF 下载、读回校验和复位。工程通过 `config` 显式提供执行系统、解释器、芯片、探针、通信速率、固件和存储布局；插件校验配置后运行对应后端，不解析 `board.lua`，也不安装 Python 或 pyOCD。

插件可独立调用，也可由 xdtc 的 action `entry` 导入。xdtc 负责展开和选择数据，x-pyocd 负责执行；芯片 DFP 和 pyOCD 兼容脚本由工程或 MSP 提供。串口交互由独立的 [x-console](https://github.com/wmem/x-console) 提供。

## 安装与入口

需要支持 Addon 的 Xmake，以及执行系统中已安装 pyOCD 的 Python 环境。Windows 后端还需要 PowerShell；从 WSL 调用 Windows 时需配置 `wslpath`，用于转换传给 Windows 的资源路径。

本地开发时，在本仓库根目录准备并安装插件；输出目录须尚不存在：

```sh
xmake lua scripts/prepare-addon.lua /tmp/x-pyocd-addon-stage
xmake addon --install /tmp/x-pyocd-addon-stage
```

这里安装的是 Xmake 插件，不会安装其运行依赖。当前 0.1.0 处于本地开发与验证阶段，尚未远程发布。配方发布后，可在消费工程中声明：

```lua
add_repositories("kunyi git@github.com:wmem/xmake-addons.git")
add_addons("x-pyocd 0.1.0")
```

版本化消费需要索引仓库中的配方和对应源码提交；仓库源码已存在不代表远程配方已发布。

安装后，在消费工程目录执行：

```sh
xmake x-pyocd --config=download.json --dry-run flash
xmake x-pyocd --config=download.json flash
xmake x-pyocd --config=download.json reset
xmake x-pyocd --config=download.json probes
```

`--dry-run` 校验并输出配置，不构建、不连接探针。它不能证明解释器、芯片资源和硬件可用。实际执行失败时以非零状态退出。

## 通过 config 指定执行方式

下面是 Linux 下载配置的形状示例。路径、芯片、探针和 Flash 边界均须替换为工程真实值；数字是示例值，不是板级默认值。

```lua
local config = {
    host = "linux",
    runtime = {python = "/path/to/prepared/python"},
    project_root = "/path/to/project",
    report_dir = "/path/to/project/build/download-reports",
    chip = "<chip>",
    lock_namespace = "my-board-probe",
    debug = {
        probe = "<probe-id>",
        frequency = 100000,       -- Hz
        protocol = "swd",
        connect = "under-reset",
    },
    pyocd = {
        target_override = "<pyocd-target>",
        ["cache.enable_memory"] = false,
        ["cache.read_code_from_elf"] = false,
        -- pack = {"/path/to/vendor.pack"},
        -- user_script = "/path/to/compat.py",
    },
    artifacts = {elf = "/path/to/project/build/firmware.elf"},
    memory = {flash = {start = 0x08000000, size = 1024 * 1024}},
    flash = {verify = true, frequencies = {100000, 50000}},
    -- 可选：先由 Xmake 构建指定 target，再执行下载。
    -- build = {target = "firmware", jobs = 8},
}
```

CLI 接收相同字段的 JSON；JSON 中使用十进制地址，且不能包含 Lua 注释或表达式。Lua API 直接接收 table。建议资源路径使用绝对路径；插件不把普通配置字符串自动解释成板描述相对路径。Linux 后端要求 Xmake 运行在 Linux，探针也须在 Linux 可见。

Windows 下载使用相同硬件配置，替换执行环境：

```lua
config.host = "windows"
config.runtime = {
    python = {uv = "uv", tool = "pyocd"},
    powershell = "powershell.exe",
    wslpath = "wslpath", -- 从 WSL 执行时必填；Windows 原生不需要。
}
```

此处 `uv` 是 Windows 中已有的 uv 命令。插件用 `uv tool dir` 查找已安装 `pyocd` 工具环境中的 `Scripts/python.exe`，不会执行 `uv tool install`。也可以直接提供 Windows Python 路径，或 `{program = "<命令>", args = {...}}`；Linux 的 Python 同样支持字符串或 `program/args` 形式。

从 WSL 执行时，插件转换 `project_root`、`report_dir`、固件路径、`pyocd.pack` 和 `pyocd.user_script`，再调用 Windows PowerShell 和 Python。探针由 Windows pyOCD 访问，不要求透传到 WSL。

## 操作与配置合同

完整校验由 [api.lua](addon/modules/api.lua) 的 `plan(config, operation)` 维护。以下为按操作查询的摘要：

| 操作 | 配置与行为 |
| --- | --- |
| `probes` | 需要 `host/runtime/project_root/report_dir`；列出执行系统可见探针，输出含 `probes` 数组的 JSON，不自动选择探针 |
| `reset` | 另需 `chip/lock_namespace/debug/pyocd`；连接所选探针并复位，使用 `debug.frequency` |
| `flash` | 另需 `artifacts.elf/memory.flash/flash`；检查 ELF 的 Flash 加载段，按频率列表尝试编程、可选实读校验，成功后复位运行 |

`debug.probe` 按探针 ID 的不区分大小写子串匹配，必须恰好匹配一个探针；建议提供完整 ID。`debug.protocol` 接受 `swd/jtag`，`debug.connect` 接受 `attach/halt/pre-reset/under-reset`。速率为正整数 Hz；`flash.frequencies` 是显式、非空的尝试顺序。

`pyocd.target_override` 必填，其他 pyOCD 参数直接传给 pyOCD。若同时提供 `pyocd.frequency/dap_protocol`，它们必须与 `debug.frequency/protocol` 一致。启用 `flash.verify` 时，必须显式关闭示例中的两个读取缓存，以确保校验读取来自目标。

`memory.flash.start/size` 定义允许下载的地址范围。当前下载入口接收 ELF；其中有内容的 `PT_LOAD` 加载段必须全部落在该范围内，越界或没有有效段会失败。`build` 仅适用于 `flash`，明确指定目标和并行数；插件不会猜测目标名称或固件位置。

执行以探针 ID 和 `lock_namespace` 加锁。工程希望与旧工具共享锁时，须使用相同 namespace。编程和复位的报告写入 `report_dir` 下的独立子目录，包含 pyOCD 配置及 `result.json`；启用校验时保存各加载段读回文件。连接之前的配置、ELF 或探针选择错误可能尚未创建报告。编程、校验、重试与报告流程见 [worker.py](addon/modules/runtime/worker.py)；Windows 解释器选择见 [run.ps1](addon/modules/runtime/run.ps1)。

## 在 xdtc action 中直接调用

需要提供 action `entry` 的 xdtc 源码版本 v0.10.0 或对应的新 Addon。下面假定 `board.lua` 中的 `tools.download` 已具备上面的配置字段：

```lua
-- xdtc.lua
return {
    data = "board.lua",
    actions = {
        flash = {
            select = "tools.download",
            entry = function(config, api, ...)
                local pyocd = import("@addon.x-pyocd.api", {anonymous = true})
                return pyocd.flash(config)
            end,
        },
    },
}
```

```sh
xmake xdtc flash
```

不需要额外的转发脚本。公开 Lua 接口是 `plan(config, operation)`、`flash(config)`、`reset(config)` 和 `probes(config)`；前者仅校验并返回独立配置，后三者执行操作。硬件参数仍来自 `config`，`api` 和 action 位置参数不会自动转换成插件参数。

## 验证边界

在仓库根目录可运行以下检查；Python 环境应事先准备，命令不会安装插件的 pyOCD 运行依赖：

```sh
xmake lua tests/api_run.lua
uv run --no-project --no-sync python tests/test_worker.py
```

前者检查公开配置合同，后者使用模拟 pyOCD 后端检查下载、校验、失败重试和探针锁。检查入口见 [api_run.lua](tests/api_run.lua) 与 [test_worker.py](tests/test_worker.py)。这里给出运行方式，不把源码测试视为实板结果。

源码中的配置校验和执行流程与实际板卡验收是不同层次。准备插件、配置检查和模拟后端检查不能替代真实探针枚举、下载读回和复位验证，也不能证明远程索引已经发布。实板验证需要工程的真实配置、已准备的运行依赖和可访问探针。

2026-10-13 在 KY3133-B00／GD32F427 工程中，隔离安装后的插件通过 WSL 调用 Windows pyOCD：枚举到 WCH-Link `BC496BA027E9`，经 xdtc 内联 entry 在 100 kHz 完成 ELF 下载及 169940 bytes 实读校验，随后 entry 复位通过。报告及读回文件保存在该 MCU 工程的 `build/x-addon-hardware-evidence/flash-*/`、`reset-*/`。该结果覆盖当前芯片、探针和 Windows via WSL 配置；Linux 仍是模拟后端验证，其他芯片、探针及物理上电行为未由本轮验证覆盖，远程发布尚未完成。

许可证见 [LICENSE](LICENSE)。
