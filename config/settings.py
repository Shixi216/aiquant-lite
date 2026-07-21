from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Hermes-OPC runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Financial data
    tushare_token: str | None = Field(default=None)

    # Search
    tavily_api_key: str | None = Field(default=None)

    # Model providers, used later by Agent Router
    deepseek_api_key: str | None = Field(default=None)
    longcat_api_key: str | None = Field(default=None)
    dashscope_api_key: str | None = Field(default=None)
    xiaomi_api_key: str | None = Field(default=None)
    tokenhub_api_key: str | None = Field(default=None)

    # Local runtime
    opc_database_path: Path = Field(
        default=PROJECT_ROOT / "database" / "hermes_opc.duckdb"
    )
    opc_log_level: str = Field(default="INFO")
    opc_router_host: str = Field(default="127.0.0.1")
    opc_router_port: int = Field(default=8765)
    opc_mcp_host: str = Field(default="127.0.0.1")
    opc_mcp_port: int = Field(default=8767)
    opc_mcp_transport: str = Field(default="stdio")
    opc_no_proxy: str = Field(
        default=(
            "localhost,127.0.0.1,push2his.eastmoney.com,"
            ".eastmoney.com,.tushare.pro,.baostock.com"
        )
    )


settings = Settings()
