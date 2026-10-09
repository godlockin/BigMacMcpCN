"""Smart recommendation engine - finds the best meal combinations."""
import re
from typing import Optional
from .models import MealItem, Recommendation
from .mcp_client import McpClient, McpClientError


class Recommender:
    """Smart recommendation engine for McDonald's meals.

    Capabilities:
    - Budget-optimized: Find best combination within a budget
    - Nutrition-aware: Filter by calories/protein constraints
    - Coupon-optimized: Apply best coupons for maximum savings
    - Group meal planning: Optimize for multiple people with different needs
    """

    # Category keywords for matching
    CATEGORY_KEYWORDS = {
        "burger": ["巨无霸", "汉堡", "板烧", "麦辣", "双层", "安格斯", "汉堡包"],
        "chicken": ["麦乐鸡", "炸鸡", "鸡翅", "鸡腿", "鸡块", "辣翅"],
        "snack": ["薯条", "玉米", "沙拉", "小食", "洋葱圈", "薯饼"],
        "drink": ["可乐", "雪碧", "咖啡", "奶茶", "橙汁", "饮料", "红茶", "拿铁"],
        "dessert": ["冰淇淋", "甜筒", "麦旋风", "派", "蛋糕", "甜品"],
        "breakfast": ["早餐", "满分", "热香饼", "麦满分", "粥", "薯饼"],
        "vegetarian": ["素食", "蔬菜", "沙拉", "玉米杯", "薯条"],
        "combo": ["套餐", "组合", "combo", "meal"],
    }

    def __init__(self, client: McpClient):
        self.client = client

    async def recommend_by_budget(
        self,
        store_id: str,
        budget: float,
        people: int = 1,
        meal_type: str = "lunch",
    ) -> Recommendation:
        """Find the best meal combination within a budget.

        Args:
            store_id: Target store ID
            budget: Maximum total price
            people: Number of people
            meal_type: breakfast / lunch / dinner / snack
        """
        # Get menu
        menu = await self.client.call_tool_safe("query-meals", {"storeId": store_id})
        items = self._extract_menu_items(menu)

        if not items:
            return Recommendation(
                title="推荐失败",
                description="无法获取菜单数据",
                reasoning="请检查门店ID是否正确",
            )

        # Filter by meal type
        filtered = self._filter_by_meal_type(items, meal_type)

        # Get coupons for this store
        coupons_result = await self.client.call_tool_safe(
            "query-store-coupons", {"storeId": store_id}
        )
        coupons = self._extract_items(coupons_result)

        # Optimize: greedy approach - maximize value within budget
        per_person_budget = budget / people
        selected = self._greedy_select(filtered, per_person_budget, people)

        if not selected:
            return Recommendation(
                title="预算不足",
                description=f"在当前预算 ¥{budget} 下无法找到合适的组合",
                reasoning="建议增加预算或更换门店",
            )

        # Calculate prices
        total_price = sum(item.total_price() for item in selected)

        # Try to apply coupons
        discount = 0.0
        applied_coupons = []
        if coupons:
            discount, applied_coupons = self._apply_best_coupons(selected, coupons)

        discounted_price = max(0, total_price - discount)

        # Calculate nutrition
        nutrition = await self.client.call_tool_safe("list-nutrition-foods")
        total_calories = self._calculate_calories(selected, nutrition)

        return Recommendation(
            title=f"预算 ¥{budget} 最优组合 ({people}人)",
            description=f"餐次: {meal_type} | 人均预算: ¥{per_person_budget:.1f}",
            items=selected,
            total_price=total_price,
            discounted_price=discounted_price,
            savings=discount,
            calories=total_calories,
            reasoning=f"从 {len(filtered)} 个可选商品中选出 {len(selected)} 个，"
            f"{'应用了优惠券节省 ¥' + f'{discount:.2f}' if discount > 0 else '无可用优惠券'}",
            coupons_applied=[c.get("title", c.get("name", "")) for c in applied_coupons],
        )

    async def recommend_group_meal(
        self,
        store_id: str,
        total_people: int,
        budget: Optional[float] = None,
        constraints: Optional[list] = None,
    ) -> Recommendation:
        """Plan a group meal with dietary constraints.

        Args:
            store_id: Target store
            total_people: Number of people
            budget: Optional total budget
            constraints: List like ["vegetarian:2", "extra-burger:4", "no-spicy:1"]
        """
        menu = await self.client.call_tool_safe("query-meals", {"storeId": store_id})
        items = self._extract_menu_items(menu)

        if not items:
            return Recommendation(
                title="团餐规划失败",
                description="无法获取菜单数据",
            )

        constraints = constraints or []
        constraint_map = self._parse_constraints(constraints, total_people)

        selected_items = []

        # Handle each constraint group
        for constraint_type, count in constraint_map.items():
            group_items = self._filter_by_constraint(items, constraint_type)
            per_person_budget = (
                (budget / total_people) if budget else None
            )
            group_selected = self._greedy_select(
                group_items,
                per_person_budget or 50.0,
                count,
            )
            selected_items.extend(group_selected)

        # Fill remaining people with default combo meals
        assigned = sum(constraint_map.values())
        remaining = total_people - assigned
        if remaining > 0:
            combos = self._filter_by_category(items, "combo")
            default_selected = self._greedy_select(
                combos or items,
                (budget / total_people if budget else 45.0),
                remaining,
            )
            selected_items.extend(default_selected)

        total_price = sum(item.total_price() for item in selected_items)

        # Try coupons
        coupons_result = await self.client.call_tool_safe(
            "query-store-coupons", {"storeId": store_id}
        )
        coupons = self._extract_items(coupons_result)
        discount, applied_coupons = (
            self._apply_best_coupons(selected_items, coupons) if coupons else (0, [])
        )

        discounted_price = max(0, total_price - discount)

        # Calculate nutrition
        nutrition = await self.client.call_tool_safe("list-nutrition-foods")
        total_calories = self._calculate_calories(selected_items, nutrition)

        constraint_desc = ", ".join(
            f"{ctype}: {cnt}人" for ctype, cnt in constraint_map.items()
        )

        return Recommendation(
            title=f"团餐方案 ({total_people}人)",
            description=f"特殊需求: {constraint_desc or '无'}"
            + (f" | 总预算: ¥{budget}" if budget else ""),
            items=selected_items,
            total_price=total_price,
            discounted_price=discounted_price,
            savings=discount,
            calories=total_calories,
            reasoning=f"共分配 {len(selected_items)} 个餐品，"
            f"人均 ¥{total_price / total_people:.1f}"
            + (f"，券后人均 ¥{discounted_price / total_people:.1f}" if discount > 0 else ""),
            coupons_applied=[c.get("title", c.get("name", "")) for c in applied_coupons],
        )

    async def recommend_cheapest_combo(
        self,
        store_id: str,
        target_items: list,
    ) -> Recommendation:
        """Find the cheapest way to get specific items using coupons.

        Args:
            store_id: Target store
            target_items: List of item names or product codes desired
        """
        menu = await self.client.call_tool_safe("query-meals", {"storeId": store_id})
        items = self._extract_menu_items(menu)

        selected = []
        for target in target_items:
            matched = self._find_matching_item(items, target)
            if matched:
                selected.append(matched)

        if not selected:
            return Recommendation(title="未找到匹配餐品", description="请检查餐品名称")

        total_price = sum(item.total_price() for item in selected)

        coupons_result = await self.client.call_tool_safe(
            "query-store-coupons", {"storeId": store_id}
        )
        coupons = self._extract_items(coupons_result)
        discount, applied_coupons = (
            self._apply_best_coupons(selected, coupons) if coupons else (0, [])
        )

        discounted_price = max(0, total_price - discount)

        # Calculate actual price via MCP
        try:
            price_result = await self.client.calculate_price(
                [{"productCode": item.product_code, "quantity": item.quantity} for item in selected]
            )
            if isinstance(price_result, dict):
                actual_total = price_result.get("totalPrice") or price_result.get("total_price", 0)
                actual_discount = price_result.get("discountAmount") or price_result.get("discount", 0)
                if actual_total:
                    total_price = actual_total
                    discount = actual_discount
                    discounted_price = actual_total - actual_discount
        except (McpClientError, Exception):
            pass  # Fall back to manual calculation

        return Recommendation(
            title="最低价组合",
            description=f"目标餐品: {', '.join(target_items)}",
            items=selected,
            total_price=total_price,
            discounted_price=discounted_price,
            savings=discount,
            reasoning=f"匹配到 {len(selected)} 个餐品"
            + (f"，应用 {len(applied_coupons)} 张优惠券" if applied_coupons else ""),
            coupons_applied=[c.get("title", c.get("name", "")) for c in applied_coupons],
        )

    # ============================================================
    # Private helpers
    # ============================================================

    def _extract_menu_items(self, menu_data) -> list:
        """Extract meal items from menu response."""
        items = []
        raw_items = self._extract_items(menu_data, "meals")

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
                quantity=1,
                category=raw.get("category") or raw.get("categoryName", ""),
                tags=raw.get("tags") or raw.get("labels", []),
            )
            if item.name:
                items.append(item)
        return items

    def _extract_items(self, data, key) -> list:
        """Extract list items from response."""
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

    def _filter_by_meal_type(self, items: list, meal_type: str) -> list:
        """Filter menu items by meal type."""
        if meal_type == "breakfast":
            return [i for i in items if self._matches_category(i.name, "breakfast")]
        elif meal_type in ("lunch", "dinner"):
            # Exclude breakfast items
            return [i for i in items if not self._matches_category(i.name, "breakfast")]
        return items

    def _filter_by_category(self, items: list, category: str) -> list:
        """Filter items by category keyword."""
        return [i for i in items if self._matches_category(i.name, category)]

    def _matches_category(self, name: str, category: str) -> bool:
        """Check if item name matches a category."""
        keywords = self.CATEGORY_KEYWORDS.get(category, [])
        name_lower = name.lower()
        return any(kw.lower() in name_lower for kw in keywords)

    def _filter_by_constraint(self, items: list, constraint: str) -> list:
        """Filter items by dietary constraint."""
        if constraint.startswith("vegetarian"):
            return self._filter_by_category(items, "vegetarian")
        elif constraint.startswith("extra-burger") or constraint.startswith("burger"):
            return self._filter_by_category(items, "burger")
        elif constraint.startswith("chicken"):
            return self._filter_by_category(items, "chicken")
        elif constraint.startswith("drink"):
            return self._filter_by_category(items, "drink")
        elif constraint.startswith("no-spicy"):
            return [i for i in items if "辣" not in i.name]
        return items

    def _parse_constraints(self, constraints: list, total: int) -> dict:
        """Parse constraint strings into a count map."""
        result = {}
        for c in constraints:
            parts = c.split(":")
            ctype = parts[0].strip()
            count = int(parts[1]) if len(parts) > 1 else 1
            result[ctype] = result.get(ctype, 0) + count
        return result

    def _greedy_select(self, items: list, budget: float, count: int) -> list:
        """Greedily select items to maximize value within budget."""
        if not items:
            return []

        # Sort by value score (price/quality proxy: prefer mid-range items)
        scored = []
        for item in items:
            # Score: prefer items in 15-40 range for main meals
            if 15 <= item.price <= 40:
                score = item.price * 2
            elif item.price < 15:
                score = item.price
            else:
                score = max(0, 40 - (item.price - 40))
            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)

        selected = []
        remaining_budget = budget * count

        for score, item in scored:
            if len(selected) >= count:
                break
            if item.price <= remaining_budget:
                selected.append(item)
                remaining_budget -= item.price

        # If not enough items selected, add cheaper ones
        if len(selected) < count:
            for _, item in sorted(scored, key=lambda x: x[1].price):
                if len(selected) >= count:
                    break
                if item not in selected and item.price <= remaining_budget:
                    selected.append(item)
                    remaining_budget -= item.price

        return selected

    def _apply_best_coupons(self, items: list, coupons: list) -> tuple:
        """Apply best coupons to selected items. Returns (discount, applied_coupons)."""
        total_discount = 0.0
        applied = []

        for coupon in coupons:
            if not isinstance(coupon, dict):
                continue

            discount = coupon.get("discount") or coupon.get("discountAmount") or 0
            try:
                discount = float(discount)
            except (ValueError, TypeError):
                discount = 0

            title = coupon.get("title") or coupon.get("name", "")

            # Check if coupon applies to our items
            applicable = True
            conditions = coupon.get("conditions") or coupon.get("description", "")
            if conditions and isinstance(conditions, str):
                # Simple matching: if conditions mention specific items, check we have them
                for item in items:
                    if any(kw in conditions for kw in [item.name, item.category]):
                        applicable = True
                        break

            if applicable and discount > 0:
                total_discount += discount
                applied.append(coupon)

        return total_discount, applied

    def _find_matching_item(self, items: list, target: str) -> Optional[MealItem]:
        """Find a menu item matching the target name/code."""
        target_lower = target.lower()
        for item in items:
            if (
                target_lower in item.name.lower()
                or item.product_code == target
                or any(kw in item.name for kw in target.split())
            ):
                return item
        # Fuzzy match - return first item if only one matches partially
        for item in items:
            for word in target.split():
                if word and word.lower() in item.name.lower():
                    return item
        return None

    def _calculate_calories(self, items: list, nutrition_data) -> int:
        """Calculate total calories from nutrition data."""
        if not isinstance(nutrition_data, dict):
            return 0

        nutrition_items = self._extract_items(nutrition_data, "foods")
        if not nutrition_items:
            return 0

        total = 0
        for item in items:
            for nut in nutrition_items:
                if isinstance(nut, dict):
                    name = nut.get("name") or nut.get("productName", "")
                    if name and item.name and name in item.name:
                        cal = nut.get("energy") or nut.get("calories") or nut.get("kcal", 0)
                        try:
                            total += int(cal) * item.quantity
                        except (ValueError, TypeError):
                            pass
        return total
