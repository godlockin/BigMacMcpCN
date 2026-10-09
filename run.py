#!/usr/bin/env python3
"""McDonald's Personal Assistant - CLI entry point.

Usage:
    python3 run.py                  # Start interactive CLI
    python3 run.py --auto           # Start with auto mode (auto-claim coupons)
    python3 run.py --monitor        # Start with background monitoring
    python3 run.py --recommend "5人午餐预算200"  # One-shot recommendation
"""
import asyncio
import sys
import os

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_assistant.config import Config
from mcd_assistant.assistant import McAssistant


async def main():
    config = Config.load()

    # Parse command line args
    args = sys.argv[1:]

    if "--auto" in args:
        config.auto_mode = True
        config.save()

    if "--monitor" in args:
        # Start with monitoring enabled
        assistant = McAssistant(config)
        await assistant.client.connect()
        await assistant.monitor.start()
        await assistant.client.disconnect()

        # Now start the interactive CLI
        await assistant.start()
    elif "--recommend" in args:
        # One-shot recommendation mode
        idx = args.index("--recommend")
        request = args[idx + 1] if idx + 1 < len(args) else ""

        assistant = McAssistant(config)
        await assistant.client.connect()

        # Get nearest store
        from mcd_assistant.mcp_client import McpClient
        store_result = await assistant.client.call_tool_safe(
            "query-nearby-stores",
            {
                "lat": config.default_location.lat,
                "lng": config.default_location.lng,
            },
        )
        stores = assistant._extract_list(store_result, "stores") if isinstance(store_result, dict) else []
        store_id = ""
        if stores and isinstance(stores[0], dict):
            store_id = (
                stores[0].get("storeId")
                or stores[0].get("store_id")
                or stores[0].get("id", "")
            )

        if store_id:
            rec = await assistant.recommender.recommend_by_budget(
                store_id, 200.0, 5, "lunch"
            )
            print(rec.summary())
        else:
            print("No nearby store found")

        await assistant.client.disconnect()
    else:
        # Default: interactive CLI
        assistant = McAssistant(config)
        await assistant.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
