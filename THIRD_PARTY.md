# 第三方软件与源码边界

本仓库的 MIT License 适用于作者自行编写的 Python 代码、JASS 运行库、桥模板、测试和说明，不为任何外部软件授予许可。

使用编辑器集成需要另行取得并安装 KKWE，以及其编译链和 Warcraft III 所需组件。本仓库不分发 KKWE/World Editor、Warcraft III、YDWE、Wave、JassHelper、PJASS、Storm/StormLib 或其他相关 DLL/可执行文件，也不提供这些软件的下载镜像。

源码中出现的接口名、宏名、内部路径和兼容指纹用于与用户本机的已安装组件交互、识别经过审查的编译契约；不包含其完整实现。外部软件及商标的权利和使用条件由对应权利人决定。

`tests/fixtures/` 的编译器夹具是为测试重新编写的模拟程序。测试使用独立、临时生成的兼容配置，不会把模拟程序加入 `bootstrap/compatibility.json` 的生产信任列表。

维护者本地保存的原始编译脚本、用户地图、运行日志、第三方二进制和安装恢复资料均不属于本次发布内容。
