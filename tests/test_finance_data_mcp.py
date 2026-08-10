import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_servers.finance_data.server import mcp
from router.integration.tools import TOOL_NAMES


def test_finance_data_mcp_exposes_only_approved_tools() -> None:
    names = {tool.name for tool in mcp._tool_manager.list_tools()}

    assert names == set(TOOL_NAMES)


def test_finance_data_mcp_is_local_by_default() -> None:
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.port == 8767
    assert mcp.settings.stateless_http is True
    assert mcp.settings.json_response is True


@pytest.mark.asyncio
async def test_stdio_client_can_initialize_and_list_tools() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.finance_data.server"],
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()

    assert {tool.name for tool in result.tools} == set(TOOL_NAMES)
