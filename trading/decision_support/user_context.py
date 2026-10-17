from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserContext:
    local_user_id: str
    external_user_id: str
    channel: str
    source: str
    authenticated: bool

    def __post_init__(self) -> None:
        if not self.local_user_id.strip():
            raise ValueError("local_user_id is required")
        if not self.external_user_id.strip():
            raise ValueError("external_user_id is required")
        if not self.channel.strip():
            raise ValueError("channel is required")
        if not self.source.strip():
            raise ValueError("user context source is required")

    @classmethod
    def finance_mcp_shadow(cls) -> "UserContext":
        return cls(
            local_user_id="finance-mcp-shadow",
            external_user_id="finance-mcp-shadow",
            channel="finance-mcp",
            source="UNAUTHENTICATED_FINANCE_MCP_ISOLATION",
            authenticated=False,
        )

    @classmethod
    def authenticated_context(
        cls,
        *,
        local_user_id: str,
        external_user_id: str,
        channel: str,
        source: str,
    ) -> "UserContext":
        return cls(
            local_user_id=local_user_id,
            external_user_id=external_user_id,
            channel=channel,
            source=source,
            authenticated=True,
        )


__all__ = ["UserContext"]
