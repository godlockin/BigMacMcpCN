#!/usr/bin/env python3
"""McDonald's Deal Composer - End-to-end Demo.

Full pipeline: NL input → parse → compose deal → generate HTML report

Usage:
    python3 demo.py "10份饭，素食2份，汉堡加量4份，预算500"
    python3 demo.py  # Uses default example
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcd_assistant.config import Config
from mcd_assistant.mcp_client import McpClient
from mcd_assistant.deal_composer import DealComposer
from mcd_assistant.deal_report import DealReportGenerator


async def main():
    # Get NL request from command line or use default
    if len(sys.argv) > 1:
        nl_request = " ".join(sys.argv[1:])
    else:
        nl_request = "我今天中午要组织一个workshop，要准备10份饭，其中素食2份，汉堡加量4份，预算500"

    print(f"\n{'='*60}")
    print(f"  麦当劳 Deal Composer - 端到端 Demo")
    print(f"{'='*60}")
    print(f"\n[输入] {nl_request}")
    print(f"{'='*60}\n")

    # Initialize
    config = Config.load()
    client = McpClient(config)
    composer = DealComposer(client, config)
    report_gen = DealReportGenerator(os.path.join(os.path.dirname(__file__), "data"))

    # Connect
    print("[1/5] 连接麦当劳 MCP Server...")
    await client.connect()
    tools = await client.list_tools()
    print(f"      ✅ 已连接，{len(tools)} 个工具可用\n")

    # Compose deal
    print("[2/5] 解析自然语言需求...")
    spec = composer.parser.parse(nl_request)
    print(f"      人数: {spec.total_people}")
    print(f"      预算: ¥{spec.budget}" if spec.budget else "      预算: 未指定")
    print(f"      餐次: {spec.meal_type or '不限'}")
    print(f"      要求: {spec.constraints or '无'}")
    print(f"      备注: {spec.notes or '无'}\n")

    print("[3/5] 获取门店菜单、优惠券、积分、商城数据...")
    # The compose_deal method handles all data fetching internally
    print("      正在调用 MCP 工具获取数据...\n")

    print("[4/5] 组合最优 Deal (套餐+单点+优惠券+积分)...")
    deal = await composer.compose_deal(nl_request)
    print(f"      ✅ Deal 组合完成\n")

    # Print deal summary
    print("[5/5] 生成方案报告...")
    print(deal.summary())
    print(f"\n{'='*60}")

    # Generate HTML report
    report_path = report_gen.generate(deal, nl_request)
    print(f"\n📄 HTML 报告已生成: {report_path}")
    print(f"{'='*60}\n")

    # Also save deal as JSON
    json_path = os.path.join(os.path.dirname(__file__), "data", "last_deal.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(deal.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"📊 Deal JSON 已保存: {json_path}")

    await client.disconnect()
    print(f"\n✅ Demo 完成!")

    return report_path


if __name__ == "__main__":
    try:
        report_path = asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nDemo interrupted.")
        sys.exit(0)
