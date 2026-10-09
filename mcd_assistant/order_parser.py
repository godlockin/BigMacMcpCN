"""Natural language order parser - converts user intent into structured orders."""
import re
from typing import Optional

from .models import OrderSpec, MealItem
from .mcp_client import McpClient


class OrderParser:
    """Parse natural language food requests into structured order specs.

    Handles patterns like:
    - "10份饭，素食2份，汉堡加量4份"
    - "帮我点一个巨无霸套餐加可乐"
    - "5个人的午餐，预算200"
    - "组织workshop，10份饭，其中素食2份，汉堡加量4份，预算500"
    - "今晚3个人吃，要鸡翅和可乐"

    Extracts: people count, budget, meal type, dining mode, dietary constraints, specific items.
    """

    # Pattern definitions
    PEOPLE_PATTERNS = [
        r"(\d+)\s*个?人",
        r"(\d+)\s*份[饭餐]",
        r"(\d+)\s*份套餐",
        r"组织.*?(\d+)\s*份",
        r"(\d+)\s*位",
        r"(\d+)\s*个人吃",
    ]

    BUDGET_PATTERNS = [
        r"预算\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*[块元]\s*(?:以内|之内|左右|预算)",
        r"不超.*?(\d+)",
        r"限.*?(\d+)",
    ]

    MEAL_TYPE_PATTERNS = {
        "breakfast": [r"早餐", r"早饭", r"早上", r"morning"],
        "lunch": [r"午餐", r"午饭", r"中午", r"noon", r"lunch"],
        "dinner": [r"晚餐", r"晚饭", r"晚上", r"今晚", r"dinner", r"evening"],
        "snack": [r"下午茶", r"零食", r"小食", r"snack", r"宵夜"],
    }

    DINING_MODE_PATTERNS = {
        "delivery": [r"外送", r"外卖", r"配送", r"送餐", r"delivery"],
        "takeaway": [r"到店取", r"自取", r"外带", r"打包", r"takeaway"],
        "dine-in": [r"堂食", r"在店吃", r"dine.?in"],
    }

    CONSTRAINT_PATTERNS = {
        "vegetarian": [r"素食\s*(\d+)", r"素\s*(\d+)\s*份", r"(\d+)\s*份素", r"veg.*?(\d+)"],
        "extra-burger": [r"汉堡加量\s*(\d+)", r"加量汉堡\s*(\d+)", r"extra.?burger.*?(\d+)"],
        "chicken": [r"(\d+)\s*份鸡翅", r"鸡翅.*?(\d+)\s*份", r"要鸡翅"],
        "no-spicy": [r"不辣\s*(\d+)", r"(\d+)\s*份不辣", r"免辣\s*(\d+)", r"不要辣\s*(\d+)?", r"(\d+)\s*份不要辣"],
        "drink": [r"(\d+)\s*份饮料", r"饮料.*?(\d+)"],
    }

    SPECIFIC_ITEM_PATTERNS = [
        # "巨无霸", "麦乐鸡", "板烧", "薯条", "可乐", etc.
        r"([\u4e00-\u9fff]+(?:套餐|汉堡|鸡腿|鸡翅|鸡块|薯条|可乐|雪碧|咖啡|冰淇淋|甜筒|派|沙拉|粥|满分|三明治))",
    ]

    # Known McDonald's items for matching
    KNOWN_ITEMS = {
        "巨无霸": "big-mac",
        "麦辣鸡腿堡": "spicy-chicken",
        "板烧鸡腿堡": "grilled-chicken",
        "双层吉士汉堡": "double-cheese",
        "麦乐鸡": "mc-nuggets",
        "薯条": "fries",
        "可乐": "coke",
        "雪碧": "sprite",
        "咖啡": "coffee",
        "冰淇淋": "ice-cream",
        "甜筒": "cone",
        "麦旋风": "mc-flurry",
        "苹果派": "apple-pie",
        "双层巨无霸": "double-big-mac",
        "安格斯": "angus",
        "早餐": "breakfast-combo",
    }

    def __init__(self, client: McpClient):
        self.client = client

    def parse(self, text: str) -> OrderSpec:
        """Parse natural language text into an OrderSpec.

        This uses pattern matching + heuristic rules. For production use,
        this could be enhanced with an LLM call for more complex parsing.
        """
        spec = OrderSpec()

        # Extract people count
        spec.total_people = self._extract_people(text)

        # Extract budget
        budget = self._extract_budget(text)
        if budget:
            spec.budget = budget

        # Extract meal type
        for meal_type, patterns in self.MEAL_TYPE_PATTERNS.items():
            if any(re.search(p, text, re.IGNORECASE) for p in patterns):
                spec.meal_type = meal_type
                break

        # Extract dining mode
        for mode, patterns in self.DINING_MODE_PATTERNS.items():
            if any(re.search(p, text, re.IGNORECASE) for p in patterns):
                spec.dining_mode = mode
                break

        # Extract dietary constraints
        spec.constraints = self._extract_constraints(text, spec.total_people)

        # Extract specific item requests
        spec.items = self._extract_specific_items(text)

        # Extract notes
        spec.notes = self._extract_notes(text)

        return spec

    async def parse_and_match(
        self,
        text: str,
        store_id: Optional[str] = None,
    ) -> OrderSpec:
        """Parse text and match items to actual menu items from a store."""
        spec = self.parse(text)

        if store_id and spec.items:
            # Get actual menu and match items
            menu = await self.client.call_tool_safe("query-meals", {"storeId": store_id})
            menu_items = self._extract_menu_items(menu)

            matched_items = []
            for requested in spec.items:
                matched = self._find_in_menu(requested, menu_items)
                if matched:
                    matched_items.append(matched)
                else:
                    # Keep the original request if no match
                    matched_items.append(requested)
            spec.items = matched_items

        return spec

    def _extract_people(self, text: str) -> int:
        """Extract number of people/ portions."""
        for pattern in self.PEOPLE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 1

    def _extract_budget(self, text: str) -> Optional[float]:
        """Extract budget amount."""
        for pattern in self.BUDGET_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return float(match.group(1))
        return None

    def _extract_constraints(self, text: str, total: int) -> list:
        """Extract dietary constraints with counts."""
        constraints = []

        for constraint_type, patterns in self.CONSTRAINT_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    grp1 = match.group(1) if match.groups() else None
                    count = int(grp1) if grp1 else (total if constraint_type == "no-spicy" else 1)
                    constraints.append(f"{constraint_type}:{count}")
                    break

        # Also check for simple mentions without counts
        if "素食" in text and not any("vegetarian" in c for c in constraints):
            constraints.append("vegetarian:1")
        if ("不辣" in text or "不要辣" in text or "免辣" in text) and not any("no-spicy" in c for c in constraints):
            constraints.append(f"no-spicy:{total}")

        return constraints

    def _extract_specific_items(self, text: str) -> list:
        """Extract specific item requests from text."""
        items = []
        found_names = set()

        # Check for known items
        for item_name, item_code in self.KNOWN_ITEMS.items():
            if item_name in text:
                # Try to extract quantity
                qty_match = re.search(
                    rf"(\d+)\s*(?:份|个|杯|盒)?\s*{re.escape(item_name)}", text
                )
                if not qty_match:
                    qty_match = re.search(
                        rf"{re.escape(item_name)}.*?(\d+)\s*(?:份|个|杯|盒)", text
                    )
                qty = int(qty_match.group(1)) if qty_match else 1

                items.append(
                    MealItem(
                        product_code=item_code,
                        name=item_name,
                        quantity=qty,
                    )
                )
                found_names.add(item_name)

        # Check for generic item patterns (only items not already found)
        for pattern in self.SPECIFIC_ITEM_PATTERNS:
            matches = re.findall(pattern, text)
            for match in matches:
                # Skip if this is a substring of an already found item
                if any(match in name or name in match for name in found_names):
                    continue
                # Skip if this is the full input text (avoid over-matching)
                if len(match) > 10:
                    continue
                qty_match = re.search(
                    rf"(\d+)\s*(?:份|个|杯|盒)?\s*{re.escape(match)}", text
                )
                qty = int(qty_match.group(1)) if qty_match else 1
                items.append(MealItem(name=match, quantity=qty))
                found_names.add(match)

        return items

    def _extract_notes(self, text: str) -> str:
        """Extract additional notes/context."""
        notes = []

        if re.search(r"workshop|会议|活动|团建|聚餐", text, re.IGNORECASE):
            notes.append("团餐/活动场景")
        if re.search(r"加量|加大|升级", text, re.IGNORECASE):
            notes.append("需要加量/升级")
        if re.search(r"儿童|小孩|宝宝", text, re.IGNORECASE):
            notes.append("包含儿童餐")
        if re.search(r"急需|马上|尽快|赶时间", text, re.IGNORECASE):
            notes.append("时间紧急")

        return "; ".join(notes)

    def _extract_menu_items(self, menu_data) -> list:
        """Extract meal items from menu response."""
        items = []
        if isinstance(menu_data, dict):
            if "error" in menu_data:
                return []
            raw_items = menu_data.get("meals") or menu_data.get("data") or menu_data.get("items", [])
            if isinstance(raw_items, dict):
                raw_items = raw_items.get("meals", [])
        elif isinstance(menu_data, list):
            raw_items = menu_data
        else:
            return []

        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = MealItem(
                product_code=str(
                    raw.get("productCode")
                    or raw.get("product_code")
                    or raw.get("code")
                    or raw.get("id", "")
                ),
                name=raw.get("name") or raw.get("productName") or raw.get("title", ""),
                price=float(raw.get("price") or raw.get("salePrice") or 0),
            )
            if item.name:
                items.append(item)
        return items

    def _find_in_menu(self, requested: MealItem, menu_items: list) -> MealItem:
        """Find a requested item in the actual menu."""
        for menu_item in menu_items:
            if requested.name.lower() in menu_item.name.lower():
                menu_item.quantity = requested.quantity
                return menu_item
            # Fuzzy match
            for word in requested.name.split():
                if word and word.lower() in menu_item.name.lower():
                    menu_item.quantity = requested.quantity
                    return menu_item
        return requested
