task("x-pyocd")
set_category("plugin")
set_menu({
    usage = "xmake x-pyocd --config=<JSON> [--dry-run] <flash|reset|probes|readback>",
    description = "按显式配置运行 pyOCD",
    options = {
        { "c", "config", "kv", nil, "执行配置 JSON" },
        { nil, "dry-run", "k", nil, "只验证并输出配置，不执行硬件操作" },
        { nil, "operation", "v", nil, "flash/reset/probes/readback" },
    },
})
on_run("main")
