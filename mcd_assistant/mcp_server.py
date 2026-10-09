"""Local enhanced MCP Server - exposes smart tools wrapping the McDonald's remote server.

This MCP server provides higher-level tools that orchestrate multiple
remote MCP calls and add intelligence (recommendations, NL parsing, monitoring).
"""
import asyncio
import json
from typing import Any, Optional

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .config import Config
from .mcp_client import McpClient
from .recommender import Recommender
from .order_parser import OrderParser
from .deal_composer import DealComposer
from .decision import solve


class McAssistantServer:
    """Local MCP Server that exposes enhanced McDonald's tools.

    This server:
    1. Internally connects to the McDonald's remote MCP server
    2. Exposes higher-level tools (dashboard, smart-recommend, parse-order, etc.)
    3. Can be connected to by any MCP client (Cursor, WorkBuddy, VSCode, etc.)

    Usage in mcp.json:
    {
      "mcpServers": {
        "mcd-assistant": {
          "command": "python3",
          "args": ["/path/to/run_mcp_server.py"]
        }
      }
    }
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.client = McpClient(self.config)
        self.recommender = Recommender(self.client)
        self.parser = OrderParser(self.client)
        self.deal_composer = DealComposer(self.client, self.config)
        self._setup_handlers()

    def _setup_handlers(self):
        """Register MCP tool handlers."""

        async def list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name="mcd-decision-plan",
                    description="团餐变更决策：输入规范化完整餐快照、每人候选、预算、旧方案及锁定者；最少影响人数重规划。纯本地、无账号操作；金额为待官方核价估算。数据格式见 examples/workshop.json。",
                    inputSchema={"type": "object", "properties": {
                        "snapshot": {"type": "object", "description": "source、captured_at、store_id、options、resources、points_budget"},
                        "participants": {"type": "array", "items": {"type": "object"}},
                        "budget_cents": {"type": "integer", "minimum": 0},
                        "previous_assignments": {"type": "array", "items": {"type": "object"}},
                        "locked_people": {"type": "array", "items": {"type": "string"}},
                        "max_nodes": {"type": "integer", "minimum": 1, "maximum": 500000},
                    }, "required": ["snapshot", "participants", "budget_cents"]},
                ),
                types.Tool(
                    name="mcd-dashboard",
                    description=(
                        "获取麦当劳个人助理仪表盘：积分、优惠券、活动日历、抽奖状态等聚合信息。"
                        "当用户想查看自己的麦当劳账户概览时使用。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                types.Tool(
                    name="mcd-nearby-stores",
                    description=(
                        "根据城市和位置关键词查询附近的麦当劳门店。返回门店名称、地址、距离、营业时间等信息。"
                        "当用户想找附近的麦当劳时使用。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名，如'北京'"},
                            "keyword": {"type": "string", "description": "位置关键词，如'朝阳区'"},
                            "beType": {"type": "integer", "description": "1=到店自取, 5=得来速", "default": 1},
                        },
                    },
                ),
                types.Tool(
                    name="mcd-store-menu",
                    description=(
                        "查询指定门店的完整菜单。返回餐品名称、编码、价格、分类等信息。"
                        "需要先用 mcd-nearby-stores 获取门店编号(storeCode)。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "storeCode": {"type": "string", "description": "门店编号(storeCode)"},
                            "beType": {"type": "integer", "description": "1=到店自取, 5=得来速", "default": 1},
                            "orderType": {"type": "integer", "description": "1=到店, 2=外送", "default": 1},
                        },
                        "required": ["storeCode"],
                    },
                ),
                types.Tool(
                    name="mcd-smart-recommend",
                    description=(
                        "智能推荐引擎：根据用户需求（预算、人数、餐次、特殊要求）"
                        "自动搜索、组合和匹配最实惠的麦当劳产品组合。"
                        "支持团餐场景：如'10份饭，素食2份，汉堡加量4份'。"
                        "返回推荐组合、价格、优惠券应用、热量估算。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "request": {
                                "type": "string",
                                "description": "自然语言需求描述，如'5人午餐预算200，素食2份'",
                            },
                            "storeId": {
                                "type": "string",
                                "description": "指定门店ID（可选，默认用最近门店）",
                            },
                        },
                        "required": ["request"],
                    },
                ),
                types.Tool(
                    name="mcd-parse-order",
                    description=(
                        "自然语言订单解析器：将用户的自然语言点餐需求解析为结构化订单。"
                        "支持解析人数、预算、餐次、特殊要求（素食/加量/不辣等）、具体餐品。"
                        "如解析'组织workshop要10份饭，素食2份，汉堡加量4份'为结构化订单。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "自然语言点餐需求",
                            },
                        },
                        "required": ["text"],
                    },
                ),
                types.Tool(
                    name="mcd-current-deals",
                    description=(
                        "获取当前所有可用的优惠和活动信息，包括：营销活动日历、"
                        "麦麦省可领优惠券、我的优惠券、积分抽奖活动、积分账户。"
                        "当用户想了解有什么优惠可用时使用。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                types.Tool(
                    name="mcd-auto-claim-coupons",
                    description=(
                        "一键领取麦麦省所有当前可用的优惠券。无需指定具体优惠券，"
                        "系统自动领取用户可领的所有券。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                types.Tool(
                    name="mcd-lottery",
                    description=(
                        "查看积分抽奖活动信息并可执行抽奖。返回活动状态、奖品列表、"
                        "抽奖消耗规则和用户可用资源。可选择是否执行抽奖。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "draw": {
                                "type": "boolean",
                                "description": "是否执行抽奖（默认false，仅查看信息）",
                            },
                        },
                    },
                ),
                types.Tool(
                    name="mcd-order-history",
                    description=(
                        "查询近期到店/外送历史订单列表。返回订单状态、内容、金额等信息。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                types.Tool(
                    name="mcd-points-mall",
                    description=(
                        "查询麦麦商城可用积分兑换的商品列表。返回商品名称、所需积分、"
                        "现金价格等信息。可查看可兑换的实物或虚拟商品。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                types.Tool(
                    name="mcd-create-order",
                    description=(
                        "根据门店信息、就餐方式、商品列表创建订单，返回订单详情与支付链接。"
                        "需要先获取门店ID和餐品编码。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "storeId": {"type": "string", "description": "门店ID"},
                            "diningMode": {
                                "type": "string",
                                "description": "就餐方式：dine-in/takeaway/delivery",
                            },
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "productCode": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                    },
                                },
                                "description": "商品列表",
                            },
                        },
                        "required": ["storeId", "items"],
                    },
                ),
                types.Tool(
                    name="mcd-cancel-order",
                    description="取消点餐订单。需要提供订单ID。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "orderId": {"type": "string", "description": "订单ID"},
                        },
                        "required": ["orderId"],
                    },
                ),
                types.Tool(
                    name="mcd-nutrition",
                    description=(
                        "获取麦当劳餐品营养成分数据，包括能量、蛋白质、脂肪、碳水化合物、"
                        "钠、钙等。当用户关心热量/营养或需要搭配指定热量套餐时使用。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                types.Tool(
                    name="mcd-campaign-calendar",
                    description="查询麦当劳中国当月的营销活动日历，返回进行中、往期和未来日期的活动。",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                types.Tool(
                    name="mcd-theme-party",
                    description=(
                        "查询麦当劳主题活动（派对/品鉴会）信息。"
                        "可按城市→门店→日期→场次逐步查询并预约。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "description": "操作类型：city/store/date/session",
                                "enum": ["city", "store", "date", "session"],
                            },
                            "city": {"type": "string", "description": "城市名（store操作时需要）"},
                            "storeId": {"type": "string", "description": "门店ID（date/session操作时需要）"},
                            "date": {"type": "string", "description": "日期（session操作时需要）"},
                        },
                        "required": ["action"],
                    },
                ),
                types.Tool(
                    name="mcd-compose-deal",
                    description=(
                        "Deal 组合引擎：根据自然语言需求，智能组合套餐(combo) + 单点(single) + "
                        "优惠券(coupon) + 积分兑换(points) 形成一个完整的 deal 方案。"
                        "\n\n工作流程：\n"
                        "1. 解析自然语言需求（人数、预算、餐次、特殊要求）\n"
                        "2. 并行获取门店菜单、用户优惠券、可领优惠券、积分账户、积分商城\n"
                        "3. 分类菜单并按约束分组分配\n"
                        "4. 优先使用套餐(更划算)，再补充单点\n"
                        "5. 应用最佳匹配的优惠券\n"
                        "6. 尝试用积分兑换免费商品\n"
                        "7. 计算价格、节省、匹配度\n\n"
                        "如果无法凑出完美方案，会输出最接近的 deal 并标注缺口供用户 review。"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "request": {
                                "type": "string",
                                "description": (
                                    "自然语言需求描述，如"
                                    "'我今天中午要组织一个workshop，要准备10份饭，其中素食2份，汉堡加量4份，预算500'"
                                ),
                            },
                            "storeId": {
                                "type": "string",
                                "description": "指定门店ID（可选，默认自动选择最近门店）",
                            },
                        },
                        "required": ["request"],
                    },
                ),
            ]

        async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
            """Handle tool calls."""
            try:
                if name != "mcd-decision-plan":
                    await self.client.ensure_connected()

                result = await self._dispatch(name, arguments)
                text = json.dumps(result, ensure_ascii=False, indent=2) if not isinstance(result, str) else result

                return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

            except Exception as e:
                return types.CallToolResult(is_error=True, content=[types.TextContent(
                    type="text",
                    text=json.dumps({"error": str(e), "tool": name}, ensure_ascii=False),
                )])

        async def on_list_tools(context: object, params: object) -> types.ListToolsResult:
            return types.ListToolsResult(tools=await list_tools())

        async def on_call_tool(context: object, params: types.CallToolRequestParams) -> types.CallToolResult:
            return await call_tool(params.name, params.arguments or {})

        self.server = Server("mcd-assistant", version="2.0.0",
                             on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    async def _dispatch(self, name: str, args: dict) -> Any:
        """Route tool call to the appropriate handler."""
        if name == "mcd-decision-plan":
            return solve(args)
        if name == "mcd-dashboard":
            return await self._tool_dashboard()
        elif name == "mcd-nearby-stores":
            loc = self.config.default_location
            city = args.get("city", loc.city)
            keyword = args.get("keyword", loc.keyword)
            be_type = args.get("beType", loc.be_type)
            return await self.client.call_tool("query-nearby-stores", {
                "searchType": 2, "city": city, "keyword": keyword, "beType": be_type
            })
        elif name == "mcd-store-menu":
            loc = self.config.default_location
            store_code = args.get("storeCode", args.get("storeId", ""))
            return await self.client.call_tool("query-meals", {
                "storeCode": store_code,
                "beType": args.get("beType", loc.be_type),
                "orderType": args.get("orderType", 1),
            })
        elif name == "mcd-smart-recommend":
            return await self._tool_smart_recommend(args)
        elif name == "mcd-parse-order":
            return await self._tool_parse_order(args)
        elif name == "mcd-current-deals":
            return await self.client.get_all_available_deals()
        elif name == "mcd-auto-claim-coupons":
            return await self.client.call_tool("auto-bind-coupons")
        elif name == "mcd-lottery":
            info = await self.client.call_tool_safe("query-lottery-info")
            if args.get("draw"):
                draw_result = await self.client.call_tool_safe("draw-lottery")
                return {"info": info, "draw_result": draw_result}
            return info
        elif name == "mcd-order-history":
            return await self.client.call_tool("order-list")
        elif name == "mcd-points-mall":
            account = await self.client.call_tool_safe("query-my-account")
            products = await self.client.call_tool_safe("mall-points-products")
            return {"account": account, "products": products}
        elif name == "mcd-create-order":
            return await self.client.call_tool("create-order", args)
        elif name == "mcd-cancel-order":
            return await self.client.call_tool("cancel-order", {"orderId": args["orderId"]})
        elif name == "mcd-nutrition":
            return await self.client.call_tool("list-nutrition-foods")
        elif name == "mcd-campaign-calendar":
            return await self.client.call_tool("campaign-calendar")
        elif name == "mcd-theme-party":
            return await self._tool_theme_party(args)
        elif name == "mcd-compose-deal":
            return await self._tool_compose_deal(args)
        else:
            return {"error": f"Unknown tool: {name}"}

    async def _tool_dashboard(self) -> dict:
        """Aggregate dashboard data."""
        account = await self.client.call_tool_safe("query-my-account")
        coupons = await self.client.call_tool_safe("query-my-coupons")
        time_info = await self.client.call_tool_safe("now-time-info")
        campaigns = await self.client.call_tool_safe("campaign-calendar")
        lottery = await self.client.call_tool_safe("query-lottery-info")

        # Get nearby stores
        stores = await self._query_nearby_stores()

        return {
            "account": account,
            "my_coupons": coupons,
            "current_time": time_info,
            "campaigns": campaigns,
            "lottery": lottery,
            "nearby_stores": stores,
        }

    async def _query_nearby_stores(self) -> str:
        """Query nearby stores using correct MCP parameters."""
        loc = self.config.default_location
        return await self.client.call_tool_safe(
            "query-nearby-stores",
            {
                "searchType": 2,
                "city": loc.city,
                "keyword": loc.keyword,
                "beType": loc.be_type,
            },
        )

    async def _get_nearest_store_code(self) -> str:
        """Get the nearest store code from nearby stores query."""
        stores_result = await self._query_nearby_stores()
        from .response_parser import ResponseParser
        stores = ResponseParser.parse_stores(stores_result)
        if stores and isinstance(stores[0], dict):
            return str(stores[0].get("storeCode") or stores[0].get("storeId") or "")
        return ""

    async def _tool_smart_recommend(self, args: dict) -> dict:
        """Smart recommendation based on NL request."""
        request = args.get("request", "")
        store_id = args.get("storeId") or args.get("storeCode", "")

        # Parse the request
        spec = self.parser.parse(request)

        # Get nearest store if not specified
        if not store_id:
            store_id = await self._get_nearest_store_code()

        if not store_id:
            return {"error": "No store available for recommendation"}

        # Generate recommendation
        if spec.budget and spec.total_people:
            rec = await self.recommender.recommend_by_budget(
                store_id, spec.budget, spec.total_people, spec.meal_type or "lunch"
            )
        elif spec.constraints:
            rec = await self.recommender.recommend_group_meal(
                store_id, spec.total_people, spec.budget, spec.constraints
            )
        elif spec.items:
            rec = await self.recommender.recommend_cheapest_combo(
                store_id, [i.name for i in spec.items]
            )
        else:
            rec = await self.recommender.recommend_by_budget(
                store_id, 100.0, spec.total_people, spec.meal_type or "lunch"
            )

        return {
            "parsed_spec": {
                "total_people": spec.total_people,
                "budget": spec.budget,
                "meal_type": spec.meal_type,
                "dining_mode": spec.dining_mode,
                "constraints": spec.constraints,
                "notes": spec.notes,
            },
            "recommendation": {
                "title": rec.title,
                "description": rec.description,
                "items": [
                    {
                        "name": i.name,
                        "product_code": i.product_code,
                        "price": i.price,
                        "quantity": i.quantity,
                        "total": i.total_price(),
                    }
                    for i in rec.items
                ],
                "total_price": rec.total_price,
                "discounted_price": rec.discounted_price,
                "savings": rec.savings,
                "calories": rec.calories,
                "reasoning": rec.reasoning,
                "coupons_applied": rec.coupons_applied,
            },
            "store_id": store_id,
        }

    async def _tool_parse_order(self, args: dict) -> dict:
        """Parse natural language order request."""
        text = args.get("text", "")

        # Get nearest store for menu matching
        store_id = await self._get_nearest_store_code()

        spec = await self.parser.parse_and_match(text, store_id)

        # Calculate price if we have items
        price_estimate = None
        if store_id and spec.items:
            price_result = await self.client.call_tool_safe(
                "calculate-price",
                {
                    "items": [
                        {"productCode": i.product_code, "quantity": i.quantity}
                        for i in spec.items
                        if i.product_code
                    ],
                    "storeId": store_id,
                },
            )
            if isinstance(price_result, dict) and "error" not in price_result:
                price_estimate = price_result

        return {
            "parsed": {
                "total_people": spec.total_people,
                "budget": spec.budget,
                "meal_type": spec.meal_type,
                "dining_mode": spec.dining_mode,
                "constraints": spec.constraints,
                "items": [
                    {
                        "name": i.name,
                        "product_code": i.product_code,
                        "price": i.price,
                        "quantity": i.quantity,
                    }
                    for i in spec.items
                ],
                "notes": spec.notes,
            },
            "price_estimate": price_estimate,
            "store_id": store_id,
        }

    async def _tool_theme_party(self, args: dict) -> dict:
        """Theme party queries."""
        action = args.get("action", "city")

        if action == "city":
            return await self.client.call_tool("query-party-city")
        elif action == "store":
            return await self.client.call_tool("query-party-store", {"city": args.get("city", "")})
        elif action == "date":
            return await self.client.call_tool("query-partystore-date", {"storeId": args.get("storeId", "")})
        elif action == "session":
            return await self.client.call_tool(
                "query-partystore-session",
                {"storeId": args.get("storeId", ""), "date": args.get("date", "")},
            )
        return {"error": f"Unknown party action: {action}"}

    async def _tool_compose_deal(self, args: dict) -> dict:
        """Deal composition engine: NL → parse → fetch data → optimize → Deal."""
        request = args.get("request", "")
        store_id = args.get("storeId", "")

        deal = await self.deal_composer.compose_deal(request, store_id=store_id)

        # Also generate an HTML report
        import os
        from .deal_report import DealReportGenerator
        report_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        report_gen = DealReportGenerator(report_dir)
        report_path = report_gen.generate(deal, request)

        result = deal.to_dict()
        result["html_report"] = report_path
        return result

    @staticmethod
    def _extract_list(data, key) -> list:
        """Extract list from response."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if key in data and isinstance(data[key], list):
                return data[key]
            for wrapper in ["data", "result", "items", "list"]:
                if wrapper in data:
                    inner = data[wrapper]
                    if isinstance(inner, list):
                        return inner
                    if isinstance(inner, dict) and key in inner:
                        return inner[key] if isinstance(inner[key], list) else []
        return []

    async def run(self):
        """Run the MCP server in stdio mode."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.server.create_initialization_options())
