# 首次运行指南

1. 启动 `HermesOPC.exe`。
2. 选择用户数据目录。默认目录是 `%LOCALAPPDATA%\HermesOPC`，不会写入安装目录。
3. 可选择导入既有 DuckDB 副本。导入前会只读打开并核对 Migration。
4. Provider 凭据优先写入 Windows Credential Manager，不会显示在普通配置或日志中。
5. 启动 Router 与 Data Hub 后再运行本地诊断。

本产品仅用于A股研究、扫描、回测、模拟和人工成交记录，不支持实盘下单。
