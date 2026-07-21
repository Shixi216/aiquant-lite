from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RouterSettings(BaseSettings):
    """Configuration for model providers used by Agent Router."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    longcat_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPC_LONGCAT_API_KEY",
            "LONGCAT_API_KEY",
        ),
    )
    longcat_base_url: str = Field(
        default="https://api.longcat.chat/openai/v1",
        validation_alias=AliasChoices(
            "OPC_LONGCAT_BASE_URL",
            "LONGCAT_BASE_URL",
        ),
    )
    longcat_model: str = Field(
        default="LongCat-2.0",
        validation_alias=AliasChoices(
            "OPC_LONGCAT_MODEL",
            "LONGCAT_MODEL",
        ),
    )

    qwen_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPC_QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
            "QWEN_API_KEY",
        ),
    )
    qwen_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPC_QWEN_BASE_URL",
            "DASHSCOPE_BASE_URL",
            "QWEN_BASE_URL",
        ),
    )
    qwen_model: str = Field(
        default="qwen3.7-plus",
        validation_alias=AliasChoices(
            "OPC_QWEN_MODEL",
            "QWEN_MODEL",
        ),
    )

    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPC_DEEPSEEK_API_KEY",
            "DEEPSEEK_API_KEY",
        ),
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices(
            "OPC_DEEPSEEK_BASE_URL",
            "DEEPSEEK_BASE_URL",
        ),
    )
    deepseek_model: str = Field(
        default="deepseek-v4-pro",
        validation_alias=AliasChoices(
            "OPC_DEEPSEEK_MODEL",
            "DEEPSEEK_MODEL",
        ),
    )

    mimo_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPC_MIMO_API_KEY",
            "MIMO_API_KEY",
            "XIAOMI_API_KEY",
        ),
    )
    mimo_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPC_MIMO_BASE_URL",
            "MIMO_BASE_URL",
            "XIAOMI_BASE_URL",
        ),
    )
    mimo_model: str = Field(
        default="mimo-v2.5",
        validation_alias=AliasChoices(
            "OPC_MIMO_MODEL",
            "MIMO_MODEL",
        ),
    )

    @property
    def longcat_ready(self) -> bool:
        if self.longcat_api_key is None:
            return False

        return bool(
            self.longcat_api_key.get_secret_value().strip()
            and self.longcat_base_url.strip()
            and self.longcat_model.strip()
        )

    @property
    def qwen_ready(self) -> bool:
        if (
            self.qwen_api_key is None
            or self.qwen_base_url is None
        ):
            return False

        return bool(
            self.qwen_api_key.get_secret_value().strip()
            and self.qwen_base_url.strip()
            and self.qwen_model.strip()
        )

    @property
    def deepseek_ready(self) -> bool:
        if self.deepseek_api_key is None:
            return False
        return bool(
            self.deepseek_api_key.get_secret_value().strip()
            and self.deepseek_base_url.strip()
            and self.deepseek_model.strip()
        )

    @property
    def mimo_ready(self) -> bool:
        if self.mimo_api_key is None or self.mimo_base_url is None:
            return False
        return bool(
            self.mimo_api_key.get_secret_value().strip()
            and self.mimo_base_url.strip()
            and self.mimo_model.strip()
        )


router_settings = RouterSettings()
