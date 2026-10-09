#!/usr/bin/env python3
"""McDonald's Assistant MCP Server entry point.

Run this as a local MCP server to expose enhanced McDonald's tools
to any MCP client (Cursor, WorkBuddy, VSCode, etc.).

MCP config example:
{
  "mcpServers": {
    "mcd-assistant": {
      "command": "python3",
      "args": ["/path/to/mcd-assistant/run_mcp_server.py"]
    }
  }
}
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_assistant.config import Config
from mcd_assistant.mcp_server import McAssistantServer


async def main():
    config = Config.load()
    server = McAssistantServer(config)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
