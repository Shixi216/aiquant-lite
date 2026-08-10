# 升级说明

使用相同 AppId 的新安装包直接覆盖程序目录。

- 不覆盖用户数据库、Credential Manager 凭据、会话或调度任务。
- 启动前检查 Migration。
- 数据库 Migration 前应先创建备份。
- Migration 失败时停止启动，不自动回滚或替换数据库。
- 当前版本和目标版本会显示在安装包及构建清单中。
