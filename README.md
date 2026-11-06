# x-pyocd 0.3.1

通过显式配置执行 pyOCD 探针枚举、固件下载、实读校验和复位，支持 Linux 与 Windows 执行环境。

正式文档从[文档首页](docs/index.mdoc)进入；[使用文档](docs/usage/index.mdoc)按任务组织，[查询参考](docs/usage/reference/index.mdoc)保存精确规则，[开发文档](docs/development/index.mdoc)说明实现与验证。

## 首次使用

在支持 Addon 的 Xmake 工程中声明：

```lua
add_repositories("kunyi git@github.com:wmem/xmake-addons.git")
add_addons("x-pyocd 0.3.1")
```

执行 xmake f -y 后，按[快速开始](docs/usage/quickstart.mdoc)准备配置并运行。

## 本地文档

已全局安装 edoc 时，在本仓库根目录执行：

```sh
edoc validate
edoc
```

打开 output/index.html 阅读离线文档。正文与配置位于 docs/，各层 index 维护顺序；修改源文后重新生成。

## 开发与验证

```sh
xmake lua tests/api_run.lua
```

上述命令在仓库根执行。完整依赖、测试入口及验证范围见[开发文档](docs/development/index.mdoc)。
