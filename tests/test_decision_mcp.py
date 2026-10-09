"""Real stdio protocol test; no remote service and no account credentials."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_local_mcp_decision_without_token():
    root = Path(__file__).resolve().parents[1]
    request = json.loads((root / "examples/workshop.json").read_text())

    async def check():
        parameters = StdioServerParameters(command=sys.executable,
                                            args=[str(root / "run_mcp_server.py")],
                                            env={"MCD_MCP_TOKEN": "", "PYTHONDONTWRITEBYTECODE": "1"})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "mcd-decision-plan" in {tool.name for tool in tools.tools}
                result = await session.call_tool("mcd-decision-plan", request)
                assert not result.is_error
                value = json.loads(result.content[0].text)
                assert value["status"] == "optimal"
                assert len(value["assignments"]) == 10
                request["previous_assignments"] = value["assignments"]
                request["participants"].pop()
                request["participants"][2]["required_tags"] = ["no-spicy"]
                revised = await session.call_tool("mcd-decision-plan", request)
                value = json.loads(revised.content[0].text)
                assert value["affected_people"] == 1
                assert len(value["assignments"]) == 9

    asyncio.run(asyncio.wait_for(check(), timeout=30))
