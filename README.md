# KKWE 防串值工具

为 KKWE 的逆天局部变量生成代码补上独立调用上下文，解决同一触发器重复运行、等待或回调交错时，临时数据被其他实例覆盖的问题。

工具包含 Python 脚本转换器、JASS 运行库、可卸载的编译接入和中文管理窗口。它不包含 KKWE 或 Warcraft III，也不修改编辑器 DLL、原有 `Hash.h` 或 GUI 定义。

English: a removable compiler adapter and JASS context runtime for KKWE's generated local-variable macros. The editor integration targets Windows; the core tests also run on Linux.

## 工作方式

- 普通动作使用独立上下文，等待之后仍读取本次调用的数据；普通子函数显式传递上下文。
- 同步枚举通过短期桥接共享本次上下文。计时器和动态触发器保留原有的持久共享状态。
- 标量与数组使用不同身份，数组支持完整整数下标。
- `YDLocal6*` 命名页面保留跨 UI 回调的共享状态；清空页面后仍可继续写入。
- 按 `library/scope` 区分 vJASS 的 `private/public` 函数；保留源码名称，补齐运行库编译依赖。
- 在 Wave 前转换脚本，在 Lua 模板后检查实际生成代码、回调入口和销毁解绑。支持受限的 Zinc 与字面量文字宏；无法确定语义时报告源行号并停止该阶段覆盖。

timer、trigger 或命名页面的有意共享，并不会被改成每次回调的私有状态。它也不是适用于任意自定义 JASS 的通用修复器。

## 环境要求

- Windows 与 Python **3.10 或以上**；核心代码只使用 Python 标准库。
- 使用受支持的 KKWE 普通 JASS/Wave 编译链，启用 JassHelper，关闭 cJass。
- Warcraft III 1.24 及以上的 hashtable API。既有实机验证环境为 1.27a，不能据此推断所有游戏版本都已验证。
- 使用管理窗口时需要 Tkinter。Windows 官方 Python 安装包通常包含 Tcl/Tk。

兼容性以仓库内的编译文件指纹和 Lua token 契约为准，**不是仅看编辑器版本号**。未知核心会拒绝安装或转换；不要只修改指纹来绕过兼容检查。

## 安装

将仓库保存在 KKWE 安装目录之外，避免编辑器升级覆盖源码：

```powershell
git clone https://github.com/Bzsga/kkwe-local-context.git
cd kkwe-local-context
```

下面的路径是示例，请改为包含 `share\script\compile\wave.lua` 的实际 KKWE 目录：

```powershell
$env:KKLC_EDITOR_ROOT = 'C:\Games\KKWE\KKWE插件'
python -X utf8 -B .\cli.py status --editor-root $env:KKLC_EDITOR_ROOT
python -X utf8 -B .\cli.py apply --editor-root $env:KKLC_EDITOR_ROOT
```

也可启动中文管理窗口，选择 KKWE 目录并点击“安装 / 恢复”：

```powershell
python -X utf8 -B .\gui.py
```

安装只在 `wave.lua` 中插入本工具的标记块，恢复资料保存在本地 `state/`。安装后保持源码目录和 Python 路径稳定；移动目录或更换 Python 后执行 `repair`。不要把 `state/` 上传到 GitHub。

安装或移除 Lua 接入后，需要重启已打开的编辑器。仅更新外置 Python/JASS 源码时，已有接入会在下次保存启动 Python 时加载更新；地图仍须从原 GUI/源码重新生成，不能把不同版本的已转换脚本叠加处理。

## 更新、恢复和卸载

```powershell
python -X utf8 -B .\cli.py repair --editor-root $env:KKLC_EDITOR_ROOT
python -X utf8 -B .\cli.py remove --editor-root $env:KKLC_EDITOR_ROOT
```

`repair` 只恢复已识别的兼容接入，保留用户及官方文件中的其他改动。`remove` 移除本工具接入，并持久关闭该编辑器的启动自动恢复；再次显式安装才重新启用。保留 `state/` 中的恢复资料，直至确认不再需要。

可通过启动入口在打开编辑器前检查接入，并按自动恢复偏好恢复兼容接入；加 `-Launch` 才启动编辑器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch.ps1 -EditorRoot $env:KKLC_EDITOR_ROOT -Launch
```

工具不会自动保存地图，也没有常驻守护进程或定时任务。绕过这个启动入口时，编辑器升级覆盖接入后需手动执行恢复。

## 开发与验证

需要可以直接打开和运行的例子时，见 [GUI 触发器演示图](examples/gui-context-demo/README.md)。其中包含 2026-10-28 已编译的 `.w3x`、7 个可展开 GUI 触发器、测试预期和历史验证说明；验证当前源码需在启用工具后重新保存编译。

不需要安装编辑器即可运行源码和模拟 native 的单元测试：

```powershell
python -X utf8 -B -m unittest discover -s tests -v
```

Lua 桥测试可选用 Lua 5.3/5.4，将 `KKLC_LUA` 指向解释器。缺少 Lua、Tk 或 Windows PowerShell 时，对应可选测试会显示跳过；这不等于那些集成已在当前机器通过。

在 Windows 上安装了受支持的 KKWE 后，还可运行隔离的脚本编译检查：

```powershell
python -X utf8 -B .\cli.py verify --editor-root $env:KKLC_EDITOR_ROOT
```

该入口调用本机 Wave、JassHelper 和 PJASS，只生成测试脚本文本，不创建地图或启动游戏。维护者曾用真实保存输入验证完整编译接入，早期演示也有实机证据；CI 的合成测试与 native mocks 不代表当前版本已在所有真实地图、游戏版本或多人环境通过。

只读转换实验请始终提供新输出路径：

```powershell
python -X utf8 -B .\cli.py transform --input .\fixtures\representative.j --output .\runs\example.j --editor-root $env:KKLC_EDITOR_ROOT
```

`transform` 省略 `--output` 会替换指定脚本文本；`validate` 也可能改写最终脚本。两者只接受文本脚本，不接受地图或二进制文件。

## 支持边界

常见的 GUI 生成模式之外，以下结构需要修正源码或定向适配：

- 直接操作旧 `YDLOC` 或上下文索引，未知 `YDLocal*` 宏，动态或转义的局部变量名称。
- 无法证明来源的异步回调、同步枚举中等待、参数准备与派发跨条件或循环路径。
- 混用动态 Condition 与普通 Action、无法追踪的句柄 getter、helper 的不合法代码引用。
- 未支持的文字宏形式，以及后续 `//! import/inject/external` 等生成路径。
- Zinc 中的逆天宏、对改签名 helper 的引用，或对相关 native 的全局重定义。

后期通用库中的动态 `ExecuteFunc` 在可确定全局 helper 名称时使用精确入口保护；普通目标继续执行，缺少上下文的 helper/桥目标被拦截。无法确定最终名称的作用域 helper 动态入口仍拒绝。

运行库的生命周期与所有权契约见 [runtime/API.md](runtime/API.md)。报错不表示地图损坏；请附上脱敏后的最小生成脚本、错误行和编译配置，避免提交完整私人地图。

## 文件结构与来源

| 路径 | 用途 |
| --- | --- |
| `adapter.py` / `final_output.py` | 前置转换和最终生成代码检查 |
| `runtime/` | 自有 JASS 运行库与所有权契约 |
| `manager.py` / `bootstrap/` | 可恢复的接入管理与桥模板 |
| `gui.py` / `launch.ps1` | 中文管理窗口与显式启动入口 |
| `tests/` / `fixtures/` / `verification/` | 原创合成测试、native mocks 与验证脚本 |
| `examples/gui-context-demo/` | 明确公开的 GUI 演示地图与使用说明 |

本仓库不分发 KKWE、YDWE、Warcraft III、Wave、JassHelper、PJASS 的程序或完整原始编译脚本，不包含私人地图、业务源码、运行日志和安装备份；唯一明确公开的演示地图位于上述 `examples/` 目录。测试编译器夹具为原创模拟实现，不会被加入生产受信任指纹。第三方软件与商标仍遵循其各自的许可及权利，详见 [THIRD_PARTY.md](THIRD_PARTY.md)。

自有源码按 [MIT License](LICENSE) 开源。
