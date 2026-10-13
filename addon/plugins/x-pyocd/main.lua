import("core.base.option")
import("core.base.json")
function main()
    assert(option.get("config"), "请提供 --config=<JSON>")
    local api = import("api", {rootdir = path.absolute("../../modules", os.scriptdir()), anonymous = true})
    local config = json.loadfile(option.get("config"))
    local operation = option.get("operation")
    if option.get("dry-run") then print(json.encode(api.plan(config, operation)))
    else api.run(config, operation) end
end
