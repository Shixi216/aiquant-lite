from config.settings import PROJECT_ROOT, settings


def configured(value: str | None) -> bool:
    return bool(value and value.strip())


print(f"项目目录: {PROJECT_ROOT}")
print(f"数据库路径: {settings.opc_database_path}")
print(f"日志级别: {settings.opc_log_level}")
print(f"Router 地址: {settings.opc_router_host}:{settings.opc_router_port}")
print(f"Tushare Token 已配置: {configured(settings.tushare_token)}")

assert settings.opc_router_port > 0
assert settings.opc_database_path.suffix == ".duckdb"
assert configured(settings.tushare_token), "TUSHARE_TOKEN 尚未填写"

settings.opc_database_path.parent.mkdir(parents=True, exist_ok=True)

print("配置读取检查通过")
