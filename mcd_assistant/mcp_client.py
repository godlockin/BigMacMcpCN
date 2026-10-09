"""McDonald's remote MCP Server client.

Connects to https://mcp.mcd.cn via Streamable HTTP protocol,
provides async access to all 33 McDonald's MCP tools.
"""
import asyncio
import json
import time
from contextlib import AsyncExitStack
from typing import Any, Optional

import httpx

from .config import Config
from .models import UserProfile


class McpClientError(Exception):
    """MCP client error."""
    pass


class McpRateLimitError(McpClientError):
    """Rate limit exceeded."""
    pass


class McpAuthError(McpClientError):
    """Authentication error."""
    pass


class McpClient:
    """Async client for McDonald's remote MCP Server.

    Manages session lifecycle, handles rate limiting (600/min),
    and provides typed access to all available tools.
    """

    def __init__(self, config: Config):
        self.config = config
        self._session = None
        self._cm_stack: Optional[AsyncExitStack] = None
        self._tools_cache: Optional[list] = None
        self._request_count = 0
        self._request_window_start = time.time()
        self._max_requests_per_min = 550  # Safety margin below 600
        self._connected = False

    async def connect(self) -> bool:
        """Establish MCP session with the remote server."""
        if self._connected and self._session:
            return True

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import (
                streamable_http_client,
                create_mcp_http_client,
            )
        except ImportError as e:
            raise McpClientError(
                "MCP SDK not installed or incompatible version. Run: pip install mcp>=2.3.0"
            ) from e

        self._cm_stack = AsyncExitStack()
        await self._cm_stack.__aenter__()

        try:
            # Create HTTP client with auth headers
            http_client = create_mcp_http_client(
                headers={
                    "Authorization": f"Bearer {self.config.mcp_token}"
                }
            )

            transport = await self._cm_stack.enter_async_context(
                streamable_http_client(
                    url=self.config.mcp_server_url,
                    http_client=http_client,
                )
            )
            read, write = transport

            self._session = await self._cm_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            self._connected = True
            return True
        except Exception as e:
            await self._cm_stack.__aexit__(None, None, None)
            self._cm_stack = None
            self._connected = False
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                raise McpAuthError(f"Token invalid or expired: {e}") from e
            raise McpClientError(f"Failed to connect to MCP server: {e}") from e

    async def disconnect(self):
        """Close the MCP session."""
        if self._cm_stack:
            try:
                await self._cm_stack.__aexit__(None, None, None)
            except Exception:
                pass
        self._cm_stack = None
        self._session = None
        self._connected = False

    async def ensure_connected(self):
        """Ensure we have an active session."""
        if not self._connected or not self._session:
            await self.connect()

    def _check_rate_limit(self):
        """Enforce client-side rate limiting."""
        now = time.time()
        elapsed = now - self._request_window_start
        if elapsed >= 60:
            self._request_count = 0
            self._request_window_start = now
        if self._request_count >= self._max_requests_per_min:
            sleep_time = 60 - elapsed
            if sleep_time > 0:
                raise McpRateLimitError(
                    f"Rate limit approaching. Please wait {sleep_time:.0f}s."
                )
        self._request_count += 1

    @staticmethod
    def _parse_result(result) -> Any:
        """Parse MCP tool call result into Python data."""
        if result is None:
            return None

        # MCP results have .content which is a list of content blocks
        content_list = []
        if hasattr(result, "content"):
            for block in result.content:
                if hasattr(block, "text"):
                    content_list.append(block.text)
                elif hasattr(block, "data"):
                    content_list.append(block.data)
        elif isinstance(result, str):
            content_list.append(result)
        else:
            content_list.append(str(result))

        raw_text = "\n".join(content_list)

        # Try to parse as JSON
        try:
            return json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            return raw_text

    async def call_tool(self, name: str, arguments: Optional[dict] = None) -> Any:
        """Call a McDonald's MCP tool by name.

        Args:
            name: Tool name (e.g., "query-my-account")
            arguments: Tool arguments dict

        Returns:
            Parsed result (dict if JSON, str otherwise)
        """
        await self.ensure_connected()
        self._check_rate_limit()

        result = await self._session.call_tool(name, arguments or {})

        # Check for errors in result
        if hasattr(result, "isError") and result.isError:
            err_text = self._parse_result(result)
            if "401" in str(err_text):
                raise McpAuthError(f"Authentication failed: {err_text}")
            if "429" in str(err_text):
                raise McpRateLimitError(f"Rate limited: {err_text}")
            raise McpClientError(f"Tool error: {err_text}")

        return self._parse_result(result)

    async def call_tool_safe(self, name: str, arguments: Optional[dict] = None) -> Any:
        """Call a tool and return None on error instead of raising."""
        try:
            return await self.call_tool(name, arguments)
        except (McpClientError, Exception) as e:
            return {"error": str(e), "tool": name}

    async def list_tools(self) -> list:
        """List all available MCP tools."""
        await self.ensure_connected()
        if self._tools_cache:
            return self._tools_cache

        result = await self._session.list_tools()
        tools = []
        for tool in result.tools:
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            })
        self._tools_cache = tools
        return tools

    # ============================================================
    # User & Account Tools
    # ============================================================

    async def get_user_account(self) -> dict:
        """查询用户积分账户信息 (query-my-account)."""
        return await self.call_tool("query-my-account")

    async def get_current_time(self) -> dict:
        """获取当前时间信息 (now-time-info)."""
        return await self.call_tool("now-time-info")

    async def get_my_coupons(self) -> dict:
        """查询用户可用优惠券 (query-my-coupons)."""
        return await self.call_tool("query-my-coupons")

    async def get_user_profile(self) -> UserProfile:
        """Get aggregated user profile: account + coupons + time."""
        account = await self.call_tool_safe("query-my-account")
        coupons = await self.call_tool_safe("query-my-coupons")
        time_info = await self.call_tool_safe("now-time-info")

        raw = {}
        if isinstance(account, dict) and "error" not in account:
            raw.update(account)
        raw["_coupons"] = coupons
        raw["_time"] = time_info

        return UserProfile(raw_data=raw)

    # ============================================================
    # Location & Store Tools
    # ============================================================

    async def query_nearby_stores(self, lat: float, lng: float, **kwargs) -> dict:
        """查询附近可用门店 (query-nearby-stores)."""
        return await self.call_tool("query-nearby-stores", {"lat": lat, "lng": lng, **kwargs})

    async def delivery_query_addresses(self) -> dict:
        """获取用户可配送地址列表 (delivery-query-addresses)."""
        return await self.call_tool("delivery-query-addresses")

    async def delivery_create_address(self, **kwargs) -> dict:
        """新增配送地址 (delivery-create-address)."""
        return await self.call_tool("delivery-create-address", kwargs)

    async def delivery_query_stores(self, **kwargs) -> dict:
        """查询可配送的门店列表 (delivery-query-stores)."""
        return await self.call_tool("delivery-query-stores", kwargs)

    # ============================================================
    # Menu & Meal Tools
    # ============================================================

    async def query_meals(self, store_id: str, **kwargs) -> dict:
        """查询当前可售卖的餐品列表 (query-meals)."""
        return await self.call_tool("query-meals", {"storeId": store_id, **kwargs})

    async def query_meal_detail(self, product_code: str, **kwargs) -> dict:
        """查询餐品详情 (query-meal-detail)."""
        return await self.call_tool("query-meal-detail", {"productCode": product_code, **kwargs})

    async def list_nutrition_foods(self, **kwargs) -> dict:
        """获取餐品营养信息列表 (list-nutrition-foods)."""
        return await self.call_tool("list-nutrition-foods", kwargs)

    async def query_meal_assistance(self, store_id: str, **kwargs) -> dict:
        """查询助餐服务 - 团餐场景 (query-meal-assistance)."""
        return await self.call_tool("query-meal-assistance", {"storeId": store_id, **kwargs})

    # ============================================================
    # Pricing & Ordering Tools
    # ============================================================

    async def calculate_price(self, items: list, **kwargs) -> dict:
        """商品价格计算 (calculate-price).

        Args:
            items: List of {productCode, quantity, ...} dicts
        """
        return await self.call_tool("calculate-price", {"items": items, **kwargs})

    async def query_store_coupons(self, store_id: str, **kwargs) -> dict:
        """查询门店可用优惠券 (query-store-coupons)."""
        return await self.call_tool("query-store-coupons", {"storeId": store_id, **kwargs})

    async def create_order(self, **kwargs) -> dict:
        """创建订单 (create-order)."""
        return await self.call_tool("create-order", kwargs)

    async def cancel_order(self, order_id: str, **kwargs) -> dict:
        """取消订单 (cancel-order)."""
        return await self.call_tool("cancel-order", {"orderId": order_id, **kwargs})

    async def query_order(self, order_id: str, **kwargs) -> dict:
        """查询订单详情 (query-order)."""
        return await self.call_tool("query-order", {"orderId": order_id, **kwargs})

    async def order_list(self, **kwargs) -> dict:
        """查询历史订单 (order-list)."""
        return await self.call_tool("order-list", kwargs)

    # ============================================================
    # Campaign & Coupon Tools
    # ============================================================

    async def campaign_calendar(self, **kwargs) -> dict:
        """活动日历查询 (campaign-calendar)."""
        return await self.call_tool("campaign-calendar", kwargs)

    async def available_coupons(self, **kwargs) -> dict:
        """麦麦省券列表查询 (available-coupons)."""
        return await self.call_tool("available-coupons", kwargs)

    async def auto_bind_coupons(self, **kwargs) -> dict:
        """麦麦省一键领券 (auto-bind-coupons)."""
        return await self.call_tool("auto-bind-coupons", kwargs)

    # ============================================================
    # Mall & Points Tools
    # ============================================================

    async def mall_points_products(self, **kwargs) -> dict:
        """查询麦麦商城商品列表 (mall-points-products)."""
        return await self.call_tool("mall-points-products", kwargs)

    async def mall_product_detail(self, product_id: str, **kwargs) -> dict:
        """查询商城商品详情 (mall-product-detail)."""
        return await self.call_tool("mall-product-detail", {"productId": product_id, **kwargs})

    async def mall_create_order(self, product_id: str, **kwargs) -> dict:
        """积分兑换商品下单 (mall-create-order)."""
        return await self.call_tool("mall-create-order", {"productId": product_id, **kwargs})

    async def mall_order_list(self, **kwargs) -> dict:
        """麦麦商城订单查询 (mall-order-list)."""
        return await self.call_tool("mall-order-list", kwargs)

    async def mall_order_detail(self, order_id: str, **kwargs) -> dict:
        """麦麦商城订单详情 (mall-order-detail)."""
        return await self.call_tool("mall-order-detail", {"orderId": order_id, **kwargs})

    # ============================================================
    # Lottery Tools
    # ============================================================

    async def query_lottery_info(self, **kwargs) -> dict:
        """查看积分抽奖活动信息 (query-lottery-info)."""
        return await self.call_tool("query-lottery-info", kwargs)

    async def draw_lottery(self, **kwargs) -> dict:
        """积分抽奖 (draw-lottery)."""
        return await self.call_tool("draw-lottery", kwargs)

    async def query_my_prizes(self, **kwargs) -> dict:
        """查看我的奖品 (query-my-prizes)."""
        return await self.call_tool("query-my-prizes", kwargs)

    # ============================================================
    # Theme Party / Event Tools
    # ============================================================

    async def query_party_city(self, **kwargs) -> dict:
        """主题活动城市列表 (query-party-city)."""
        return await self.call_tool("query-party-city", kwargs)

    async def query_party_store(self, city: str, **kwargs) -> dict:
        """主题活动门店列表 (query-party-store)."""
        return await self.call_tool("query-party-store", {"city": city, **kwargs})

    async def query_partystore_date(self, store_id: str, **kwargs) -> dict:
        """主题活动可预约日期 (query-partystore-date)."""
        return await self.call_tool("query-partystore-date", {"storeId": store_id, **kwargs})

    async def query_partystore_session(self, store_id: str, date: str, **kwargs) -> dict:
        """主题活动可预约场次 (query-partystore-session)."""
        return await self.call_tool(
            "query-partystore-session",
            {"storeId": store_id, "date": date, **kwargs},
        )

    async def party_order_create(self, **kwargs) -> dict:
        """主题活动订单创建 (party-order-create)."""
        return await self.call_tool("party-order-create", kwargs)

    # ============================================================
    # Aggregation helpers
    # ============================================================

    async def get_all_available_deals(self) -> dict:
        """Aggregate all current deals and promotions in one call."""
        results = {}

        # Campaign calendar
        results["campaigns"] = await self.call_tool_safe("campaign-calendar")

        # Available coupons (麦麦省)
        results["available_coupons"] = await self.call_tool_safe("available-coupons")

        # My coupons
        results["my_coupons"] = await self.call_tool_safe("query-my-coupons")

        # Lottery info
        results["lottery"] = await self.call_tool_safe("query-lottery-info")

        # Account/points
        results["account"] = await self.call_tool_safe("query-my-account")

        return results

    async def get_nearby_stores_with_menu(self, lat: float, lng: float) -> dict:
        """Get nearby stores and their menus in one operation."""
        stores = await self.call_tool_safe("query-nearby-stores", {"lat": lat, "lng": lng})

        result = {"stores": stores}

        # Try to get menu for the nearest store
        if isinstance(stores, dict) and "stores" in stores:
            store_list = stores["stores"]
            if store_list and isinstance(store_list, list):
                nearest = store_list[0]
                store_id = nearest.get("storeId") or nearest.get("store_id") or nearest.get("id", "")
                if store_id:
                    result["nearest_store_id"] = store_id
                    result["menu"] = await self.call_tool_safe("query-meals", {"storeId": store_id})
                    result["store_coupons"] = await self.call_tool_safe(
                        "query-store-coupons", {"storeId": store_id}
                    )

        return result

    @property
    def is_connected(self) -> bool:
        return self._connected
