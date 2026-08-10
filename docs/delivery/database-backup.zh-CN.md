# 数据库备份说明

备份流程：

1. 停止 Router 和 Data Hub。
2. 复制用户数据目录中的 `database\hermes_opc.duckdb`。
3. 保存 SHA-256、文件大小、时间和 Migration checksum。
4. 使用 DuckDB 只读模式打开副本。
5. 按保留策略保存多个历史备份。

桌面端“设置 → 创建数据库备份”会执行复制、SHA-256 对比、
DuckDB 只读打开和 0100—0111 Migration checksum 校验。当前保留策略为
`MANUAL_KEEP_ALL`，不会自动删除历史备份。

备份不得包含 `.env` 或明文凭据。
