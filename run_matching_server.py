#!/usr/bin/env python3
"""Model-neutral order matching MCP entrypoint. Credentials from environment."""
import asyncio
import logging

from mcd_assistant.matching_mcp import MatchingServer

if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    asyncio.run(MatchingServer().run())
