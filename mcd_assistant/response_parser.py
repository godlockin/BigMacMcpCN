"""Parse McDonald's MCP markdown responses into structured data.

The McDonald's MCP Server returns responses as markdown-formatted text
with embedded JSON. This module extracts structured data from those
responses for the Deal Composer to consume.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedMenuItem:
    """A parsed menu item from query-meals response."""
    product_code: str = ""
    name: str = ""
    price: float = 0.0
    original_price: float = 0.0
    category: str = ""
    is_combo: bool = False
    combo_items: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    calories: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedCoupon:
    """A parsed coupon from coupon queries."""
    title: str = ""
    coupon_id: str = ""
    discount_price: float = 0.0
    original_price: float = 0.0
    status: str = ""
    expiry: str = ""
    tags: list = field(default_factory=list)
    source: str = ""  # my-coupons, available, store
    raw: str = ""


@dataclass
class ParsedAccount:
    """Parsed user account info."""
    available_points: int = 0
    total_points: int = 0
    expiring_points: int = 0
    frozen_points: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedNutritionItem:
    """A parsed nutrition item."""
    name: str = ""
    energy: int = 0
    protein: float = 0.0
    fat: float = 0.0
    carbs: float = 0.0
    sodium: float = 0.0
    calcium: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedMallProduct:
    """A parsed mall product (points redeemable)."""
    product_id: str = ""
    name: str = ""
    points_cost: int = 0
    cash_price: float = 0.0
    description: str = ""
    raw: dict = field(default_factory=dict)


class ResponseParser:
    """Parse McDonald's MCP markdown responses into structured data.

    The MCP server wraps API responses in markdown with:
    1. Field description tables
    2. Embedded JSON after '## Original Response' or similar markers
    3. Structured markdown sections (## headers, - bullet points)

    This parser extracts the actual data from all three formats.
    """

    # JSON extraction patterns
    JSON_PATTERNS = [
        r'##\s*Original\s+Response\s*\n+(.*?)(?:\n##|\Z)',
        r'##\s*API\s+Response\s*\n+(.*?)(?:\n##|\Z)',
        r'```json\s*(.*?)\s*```',
        r'(\{[^{}]*"success"[^{}]*\})',
    ]

    # Coupon patterns (from query-my-coupons markdown)
    COUPON_TITLE_PATTERN = r'##\s+(.+)'
    COUPON_DISCOUNT_PATTERN = r'\*\*优惠\*\*[：:]\s*[¥￥]?([\d.]+)'
    COUPON_ORIG_PRICE_PATTERN = r'\*\*原价\*\*[：:]\s*[¥￥]?([\d.]+)'
    COUPON_EXPIRY_PATTERN = r'\*\*有效期\*\*[：:]\s*(.+)'
    COUPON_TAGS_PATTERN = r'\*\*标签\*\*[：:]\s*(.+)'
    COUPON_COUNT_PATTERN = r'共\s*(\d+)\s*张可用优惠券'

    # Available coupons (from available-coupons markdown)
    AVAIL_COUPON_TITLE_PATTERN = r'优惠券标题[：:]\s*(.+?)(?:\\|$)'
    AVAIL_COUPON_STATUS_PATTERN = r'状态[：:]\s*(.+?)(?:\\|$)'

    # Account patterns (from query-my-account)
    ACCOUNT_PATTERNS = {
        'availablePoints': r'"availablePoints"\s*:\s*(\d+)',
        'totalPoints': r'"totalPoints"\s*:\s*(\d+)',
        'expiringPoints': r'"expiringPoints"\s*:\s*(\d+)',
        'frozenPoints': r'"frozenPoints"\s*:\s*(\d+)',
    }

    # Menu patterns
    MENU_ITEM_PATTERN = r'(?:餐品名称|名称)[：:]\s*(.+?)(?:\\|$)'
    MENU_PRICE_PATTERN = r'(?:价格|售价)[：:]\s*[¥￥]?([\d.]+)'
    MENU_CODE_PATTERN = r'(?:餐品编码|商品编码|productCode|product_code)[：:]\s*"?([\w-]+)"?'
    MENU_CATEGORY_PATTERN = r'(?:分类|类别|category)[：:]\s*(.+?)(?:\\|$)'

    # Mall patterns
    MALL_PRODUCT_PATTERN = r'(?:商品名称|名称)[：:]\s*(.+?)(?:\\|$)'
    MALL_POINTS_PATTERN = r'(?:所需积分|积分|points)[：:]\s*(\d+)'
    MALL_CASH_PATTERN = r'(?:现金价格|价格|price)[：:]\s*[¥￥]?([\d.]+)'

    @classmethod
    def extract_json(cls, text: str) -> Optional[dict]:
        """Extract embedded JSON from markdown response.

        Uses a brace-counting parser that respects string boundaries
        to correctly handle nested JSON with quotes containing braces.
        """
        if not isinstance(text, str):
            return None

        # Find the main API response JSON (starts with {"success)
        search_terms = ['{"success']
        for term in search_terms:
            idx = text.find(term)
            if idx >= 0:
                parsed = cls._extract_brace_json(text, idx)
                if parsed:
                    return parsed

        # Try ## Original Response section
        for pattern in cls.JSON_PATTERNS[:2]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                # Find the JSON object within this section
                brace_start = json_str.find('{')
                if brace_start >= 0:
                    parsed = cls._extract_brace_json(json_str, brace_start)
                    if parsed:
                        return parsed

        # Try ```json code blocks
        for pattern in [r'```json\s*(.*?)\s*```', r'```\s*(\{.*?\})\s*```']:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        # Last resort: scan for any { and try brace extraction
        for i in range(len(text)):
            if text[i] == '{' and i + 1 < len(text) and text[i + 1] in '"':
                parsed = cls._extract_brace_json(text, i)
                if parsed and ('success' in parsed or 'data' in parsed or 'code' in parsed):
                    return parsed

        return None

    @classmethod
    def _extract_brace_json(cls, text: str, start: int) -> Optional[dict]:
        """Extract a JSON object starting at `start` using brace counting.

        Properly handles braces inside string literals and escaped characters.
        """
        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]

            if escape:
                escape = False
                continue

            if ch == '\\':
                escape = True
                continue

            if ch == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_str = text[start:i + 1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        return None

        return None

    @classmethod
    def parse_coupons(cls, response) -> list[ParsedCoupon]:
        """Parse coupon data from query-my-coupons response."""
        if not isinstance(response, str):
            if isinstance(response, dict):
                coupon_list = response.get("coupons") or response.get("data", {}).get("coupons", [])
                return [cls._coupon_from_dict(c, "my-coupons") for c in coupon_list if isinstance(c, dict)]
            return []

        coupons = []

        # Split by ## headers (each coupon is a section)
        sections = re.split(r'\n##\s+', response)

        for section in sections[1:]:  # Skip preamble
            lines = section.strip().split('\n')
            title = lines[0].strip() if lines else ""

            discount = cls._search_float(cls.COUPON_DISCOUNT_PATTERN, section)
            orig_price = cls._search_float(cls.COUPON_ORIG_PRICE_PATTERN, section)
            expiry = cls._search_str(cls.COUPON_EXPIRY_PATTERN, section)
            tags_str = cls._search_str(cls.COUPON_TAGS_PATTERN, section)
            tags = [t.strip() for t in tags_str.split('、')] if tags_str else []

            if title:
                coupons.append(ParsedCoupon(
                    title=title,
                    discount_price=discount,
                    original_price=orig_price,
                    status="available",
                    expiry=expiry,
                    tags=tags,
                    source="my-coupons",
                    raw=section[:200],
                ))

        return coupons

    @classmethod
    def parse_available_coupons(cls, response) -> list[ParsedCoupon]:
        """Parse coupon data from available-coupons (麦麦省) response."""
        if not isinstance(response, str):
            if isinstance(response, dict):
                coupon_list = response.get("coupons") or response.get("data", [])
                return [cls._coupon_from_dict(c, "available") for c in coupon_list if isinstance(c, dict)]
            return []

        coupons = []

        # Pattern: "- 优惠券标题：{title} \n  状态：{status}"
        titles = re.findall(cls.AVAIL_COUPON_TITLE_PATTERN, response)
        statuses = re.findall(cls.AVAIL_COUPON_STATUS_PATTERN, response)

        for i, title in enumerate(titles):
            title = title.strip()
            status = statuses[i].strip() if i < len(statuses) else ""
            if title:
                coupons.append(ParsedCoupon(
                    title=title,
                    status=status,
                    source="available",
                    raw=title,
                ))

        return coupons

    @classmethod
    def parse_account(cls, response) -> ParsedAccount:
        """Parse account info from query-my-account response."""
        if isinstance(response, dict):
            data = response.get("data") or response
            return ParsedAccount(
                available_points=data.get("availablePoints") or data.get("available_points", 0),
                total_points=data.get("totalPoints") or data.get("total_points", 0),
                expiring_points=data.get("expiringPoints") or data.get("expiring_points", 0),
                frozen_points=data.get("frozenPoints") or data.get("frozen_points", 0),
                raw=data,
            )

        if not isinstance(response, str):
            return ParsedAccount()

        # Extract from markdown with embedded JSON
        json_data = cls.extract_json(response)
        if json_data:
            data = json_data.get("data") or json_data
            if isinstance(data, dict):
                return ParsedAccount(
                    available_points=cls._safe_int(data.get("availablePoints")),
                    total_points=cls._safe_int(data.get("totalPoints")),
                    expiring_points=cls._safe_int(data.get("expiringPoints")),
                    frozen_points=cls._safe_int(data.get("frozenPoints")),
                    raw=data,
                )

        # Fallback: regex extraction
        avail = cls._search_int(cls.ACCOUNT_PATTERNS['availablePoints'], response)
        total = cls._search_int(cls.ACCOUNT_PATTERNS['totalPoints'], response)
        expiring = cls._search_int(cls.ACCOUNT_PATTERNS['expiringPoints'], response)

        return ParsedAccount(
            available_points=avail,
            total_points=total,
            expiring_points=expiring,
        )

    @classmethod
    def parse_nutrition(cls, response) -> list[ParsedNutritionItem]:
        """Parse nutrition data from list-nutrition-foods response."""
        items = []

        if isinstance(response, dict):
            food_list = response.get("data") or response.get("foods", [])
            if isinstance(food_list, str):
                # data is a JSON string
                try:
                    food_list = json.loads(food_list)
                except json.JSONDecodeError:
                    food_list = []
            for food in food_list if isinstance(food_list, list) else []:
                if isinstance(food, dict):
                    items.append(ParsedNutritionItem(
                        name=food.get("name") or food.get("productName", ""),
                        energy=cls._safe_int(food.get("energy") or food.get("calories") or food.get("kcal")),
                        protein=cls._safe_float(food.get("protein")),
                        fat=cls._safe_float(food.get("fat")),
                        carbs=cls._safe_float(food.get("carbohydrate") or food.get("carbs")),
                        sodium=cls._safe_float(food.get("sodium")),
                        calcium=cls._safe_float(food.get("calcium")),
                        raw=food,
                    ))
            return items

        if not isinstance(response, str):
            return items

        # Extract JSON from markdown
        json_data = cls.extract_json(response)
        if json_data:
            data = json_data.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = []
            if isinstance(data, list):
                for food in data:
                    if isinstance(food, dict):
                        items.append(ParsedNutritionItem(
                            name=food.get("name") or food.get("productName", ""),
                            energy=cls._safe_int(food.get("energy") or food.get("calories")),
                            protein=cls._safe_float(food.get("protein")),
                            fat=cls._safe_float(food.get("fat")),
                            carbs=cls._safe_float(food.get("carbohydrate")),
                            sodium=cls._safe_float(food.get("sodium")),
                            calcium=cls._safe_float(food.get("calcium")),
                            raw=food,
                        ))

        return items

    @classmethod
    def parse_menu(cls, response) -> list[ParsedMenuItem]:
        """Parse menu items from query-meals response.

        Response format:
          data.categories[]: [{name, meals: [{code, tags}]}]
          data.meals: {code: {name, currentPrice, originalPrice, ...}}
        """
        items = []

        if isinstance(response, dict):
            data = response.get("data") or response
            if isinstance(data, dict):
                return cls._parse_meals_data(data)
            if isinstance(data, list):
                for d in data:
                    if isinstance(d, dict):
                        items.append(cls._menu_item_from_dict(d))
            return items

        if not isinstance(response, str):
            return items

        # Extract JSON from markdown
        json_data = cls.extract_json(response)
        if json_data:
            data = json_data.get("data")
            if isinstance(data, dict):
                return cls._parse_meals_data(data)
            elif isinstance(data, list):
                for meal in data:
                    if isinstance(meal, dict):
                        items.append(cls._menu_item_from_dict(meal))

        return items

    @classmethod
    def _parse_meals_data(cls, data: dict) -> list[ParsedMenuItem]:
        """Parse the meals data structure from query-meals response.

        data.meals is a dict mapping code -> meal details.
        data.categories is a list of {name, meals: [{code, tags}]}.
        """
        items = []

        # Build category lookup: code -> category_name
        code_to_category = {}
        categories = data.get("categories", [])
        if isinstance(categories, list):
            for cat in categories:
                if isinstance(cat, dict):
                    cat_name = cat.get("name", "")
                    cat_meals = cat.get("meals", [])
                    if isinstance(cat_meals, list):
                        for m in cat_meals:
                            if isinstance(m, dict):
                                code = str(m.get("code", ""))
                                if code:
                                    code_to_category[code] = cat_name

        # Parse meals dict (code -> details)
        meals = data.get("meals", {})
        if isinstance(meals, dict):
            for code, details in meals.items():
                if isinstance(details, dict):
                    item = cls._menu_item_from_dict(details)
                    # Override code if not set in details
                    if not item.product_code:
                        item.product_code = str(code)
                    # Set category from lookup
                    if not item.category:
                        item.category = code_to_category.get(str(code), "")
                    # Detect combo from name/category
                    name_lower = item.name.lower()
                    item.is_combo = any(kw in name_lower for kw in ["套餐", "combo", "三件套", "分享餐", "八件套", "多人餐"])
                    items.append(item)
        elif isinstance(meals, list):
            for meal in meals:
                if isinstance(meal, dict):
                    items.append(cls._menu_item_from_dict(meal))

        return items

    @classmethod
    def parse_mall_products(cls, response) -> list[ParsedMallProduct]:
        """Parse mall products from mall-points-products response."""
        products = []

        if isinstance(response, dict):
            prod_list = response.get("products") or response.get("data", {}).get("products", [])
            if isinstance(prod_list, list):
                for p in prod_list:
                    if isinstance(p, dict):
                        products.append(cls._mall_product_from_dict(p))
            return products

        if not isinstance(response, str):
            return products

        # Extract JSON
        json_data = cls.extract_json(response)
        if json_data:
            data = json_data.get("data") or json_data
            if isinstance(data, dict):
                prod_list = data.get("products") or data.get("list", [])
            elif isinstance(data, list):
                prod_list = data
            else:
                prod_list = []

            if isinstance(prod_list, list):
                for p in prod_list:
                    if isinstance(p, dict):
                        products.append(cls._mall_product_from_dict(p))

        return products

    @classmethod
    def parse_campaigns(cls, response) -> list[dict]:
        """Parse campaign calendar response."""
        if isinstance(response, dict):
            return response.get("campaigns") or response.get("data", {}).get("campaigns", [])

        if not isinstance(response, str):
            return []

        campaigns = []
        # Split by #### headers
        sections = re.split(r'\n####\s+', response)

        for section in sections[1:]:
            lines = section.strip().split('\n')
            title_match = re.search(r'活动标题[：:]\s*(.+)', section)
            content_match = re.search(r'活动内容介绍[：:]\s*(.+)', section, re.DOTALL)

            title = title_match.group(1).strip() if title_match else (lines[0].strip() if lines else "")
            content = content_match.group(1).strip()[:200] if content_match else ""

            if title:
                campaigns.append({
                    "title": title,
                    "description": content,
                    "raw": section[:300],
                })

        return campaigns

    @classmethod
    def parse_store_id(cls, response) -> str:
        """Extract nearest store ID from query-nearby-stores response."""
        if isinstance(response, dict):
            stores = response.get("stores") or response.get("data", {}).get("stores", [])
            if stores and isinstance(stores[0], dict):
                return str(
                    stores[0].get("storeCode")
                    or stores[0].get("storeId")
                    or stores[0].get("store_code")
                    or stores[0].get("id", "")
                )
            return ""

        if not isinstance(response, str):
            return ""

        # Extract JSON
        json_data = cls.extract_json(response)
        if json_data:
            data = json_data.get("data") or json_data
            if isinstance(data, dict):
                store_list = data.get("stores") or data.get("list", [])
                if isinstance(store_list, list) and store_list:
                    store = store_list[0]
                    return str(
                        store.get("storeCode")
                        or store.get("storeId")
                        or store.get("store_code")
                        or store.get("id", "")
                    )

        # Fallback regex
        match = re.search(r'storeCode[：:]\s*"?([\w-]+)"?', response)
        if match:
            return match.group(1)

        match = re.search(r'门店编号[：:]\s*([\w-]+)', response)
        if match:
            return match.group(1)

        return ""

    @classmethod
    def parse_stores(cls, response) -> list[dict]:
        """Parse store list from query-nearby-stores response.

        Response format: markdown prefix + JSON with {"data": [{storeCode, storeName, ...}]}
        """
        if isinstance(response, dict):
            stores = response.get("stores") or response.get("data", {})
            if isinstance(stores, list):
                return stores
            if isinstance(stores, dict) and "stores" in stores:
                return stores["stores"]
            return []

        if not isinstance(response, str):
            return []

        json_data = cls.extract_json(response)
        if json_data:
            data = json_data.get("data")
            # data is directly a list of store objects
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                store_list = data.get("stores") or data.get("list", [])
                return store_list if isinstance(store_list, list) else []

        return []

    # ============================================================
    # Private helpers
    # ============================================================

    @staticmethod
    def _coupon_from_dict(c: dict, source: str) -> ParsedCoupon:
        return ParsedCoupon(
            title=c.get("title") or c.get("name") or c.get("couponTitle", ""),
            coupon_id=str(c.get("couponId") or c.get("id", "")),
            discount_price=float(c.get("discountPrice") or c.get("price") or c.get("discountAmount", 0) or 0),
            original_price=float(c.get("originalPrice") or c.get("faceValue", 0) or 0),
            status=c.get("status") or c.get("couponStatus", "available"),
            expiry=c.get("expiryDate") or c.get("expireDate") or c.get("endDate", ""),
            tags=c.get("tags") or c.get("labels", []),
            source=source,
            raw=str(c)[:200],
        )

    @staticmethod
    def _menu_item_from_dict(m: dict) -> ParsedMenuItem:
        name = m.get("name") or m.get("productName") or m.get("title", "")
        category = m.get("category") or m.get("categoryName") or m.get("categoryCode", "")
        # McDonald's API uses currentPrice and originalPrice
        price = float(m.get("currentPrice") or m.get("price") or m.get("salePrice") or m.get("finalPrice", 0) or 0)
        orig_price = float(m.get("originalPrice") or m.get("listPrice") or m.get("original_price", 0) or 0)
        code = str(m.get("code") or m.get("productCode") or m.get("product_code") or m.get("id", ""))
        tags = m.get("tags") or m.get("labels") or m.get("attributeTags", [])
        name_lower = name.lower()
        is_combo = any(kw in name_lower for kw in ["套餐", "combo", "三件套", "分享餐", "八件套", "多人餐"])

        combo_items = m.get("comboItems") or m.get("components") or m.get("mealItems", [])

        return ParsedMenuItem(
            product_code=code,
            name=name,
            price=price,
            original_price=orig_price,
            category=category,
            is_combo=is_combo,
            combo_items=combo_items if isinstance(combo_items, list) else [],
            tags=tags if isinstance(tags, list) else [],
            raw=m,
        )

    @staticmethod
    def _mall_product_from_dict(p: dict) -> ParsedMallProduct:
        return ParsedMallProduct(
            product_id=str(p.get("productId") or p.get("id", "")),
            name=p.get("name") or p.get("productName") or p.get("title", ""),
            points_cost=int(p.get("points") or p.get("needPoints") or p.get("exchangePoints", 0) or 0),
            cash_price=float(p.get("price") or p.get("cashPrice") or p.get("salePrice", 0) or 0),
            description=p.get("description") or p.get("productDesc", ""),
            raw=p,
        )

    @staticmethod
    def _search_float(pattern: str, text: str) -> float:
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                return 0.0
        return 0.0

    @staticmethod
    def _search_int(pattern: str, text: str) -> int:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                return 0
        return 0

    @staticmethod
    def _search_str(pattern: str, text: str) -> str:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _safe_int(val) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
