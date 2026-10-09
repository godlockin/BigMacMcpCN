"""Deal Composer - the core engine that composes optimal McDonald's deals.

Combines combos (套餐), single items (单点), coupons (优惠券), and
points redemption (积分兑换) to form a complete deal that best
matches the user's natural language request.

If a perfect deal can't be composed, outputs the closest possible
deal with explicit gap analysis for user review.
"""
import json
import re
from dataclasses import dataclass, field, replace
from typing import Optional

from .config import Config
from .mcp_client import McpClient
from .order_parser import OrderParser
from .response_parser import ResponseParser, ParsedMenuItem, ParsedCoupon, ParsedAccount, ParsedMallProduct, ParsedNutritionItem
from .models import OrderSpec, MealItem


# ============================================================
# Deal Data Models
# ============================================================

@dataclass
class DealComponent:
    """A single component in a composed deal."""
    component_type: str = ""  # combo, single, coupon, points-redemption
    item_name: str = ""
    product_code: str = ""
    quantity: int = 1
    unit_price: float = 0.0
    total_price: float = 0.0
    original_unit_price: float = 0.0  # preserved before coupon/points optimization
    original_total_price: float = 0.0
    assigned_to: str = ""  # constraint group or "default"
    coupon_applied: str = ""
    points_used: int = 0
    is_free: bool = False
    notes: str = ""

    def summary(self) -> str:
        parts = [f"{self.item_name} x{self.quantity}"]
        if self.coupon_applied:
            parts.append(f"[券:{self.coupon_applied}]")
        if self.points_used:
            parts.append(f"[积分:{self.points_used}]")
        if self.is_free:
            parts.append("[免费]")
        parts.append(f"= ¥{self.total_price:.2f}")
        return " ".join(parts)


@dataclass
class Deal:
    """A complete composed deal with full breakdown."""
    title: str = ""
    description: str = ""
    order_spec: Optional[OrderSpec] = None
    components: list[DealComponent] = field(default_factory=list)
    combo_items: list[DealComponent] = field(default_factory=list)
    single_items: list[DealComponent] = field(default_factory=list)
    coupons_applied: list[str] = field(default_factory=list)
    points_used: int = 0
    points_items: list[DealComponent] = field(default_factory=list)
    original_total: float = 0.0
    coupon_savings: float = 0.0
    points_savings: float = 0.0
    combo_savings: float = 0.0
    final_price: float = 0.0
    per_person_price: float = 0.0
    gaps: list[str] = field(default_factory=list)
    constraint_satisfaction: dict = field(default_factory=dict)
    confidence: float = 0.0
    reasoning: str = ""
    store_id: str = ""
    store_name: str = ""
    is_complete: bool = True

    def summary(self) -> str:
        lines = [f"=== {self.title} ==="]
        lines.append(self.description)
        lines.append("")

        if self.combo_items:
            lines.append("[套餐 Combo]")
            for c in self.combo_items:
                lines.append(f"  {c.summary()}")
            lines.append("")

        if self.single_items:
            lines.append("[单点 Single]")
            for c in self.single_items:
                lines.append(f"  {c.summary()}")
            lines.append("")

        if self.points_items:
            lines.append("[积分兑换 Points]")
            for c in self.points_items:
                lines.append(f"  {c.summary()}")
            lines.append("")

        if self.coupons_applied:
            lines.append(f"[优惠券] {', '.join(self.coupons_applied)}")
            lines.append("")

        lines.append(f"原价: ¥{self.original_total:.2f}")
        if self.coupon_savings > 0:
            lines.append(f"优惠券节省: -¥{self.coupon_savings:.2f}")
        if self.points_savings > 0:
            lines.append(f"积分兑换价值: -¥{self.points_savings:.2f}")
        if self.combo_savings > 0:
            lines.append(f"套餐节省: -¥{self.combo_savings:.2f}")
        lines.append(f"应付总价: ¥{self.final_price:.2f}")
        if self.order_spec and self.order_spec.total_people > 0:
            lines.append(f"人均: ¥{self.per_person_price:.2f}")
        lines.append("")

        if self.gaps:
            lines.append("[缺口 Gap]")
            for g in self.gaps:
                lines.append(f"  ! {g}")
            lines.append("")

        lines.append(f"规则检查通过比例: {self.confidence:.0%}（非下单成功率）")
        lines.append("价格为快照估算，需官方整单核价；不推断未知饮食属性或优惠适用性。")
        if self.reasoning:
            lines.append(f"方案说明: {self.reasoning}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "order_spec": {
                "total_people": self.order_spec.total_people if self.order_spec else 0,
                "budget": self.order_spec.budget if self.order_spec else None,
                "meal_type": self.order_spec.meal_type if self.order_spec else "",
                "constraints": self.order_spec.constraints if self.order_spec else [],
                "notes": self.order_spec.notes if self.order_spec else "",
            },
            "combo_items": [{"name": c.item_name, "qty": c.quantity, "price": c.total_price, "coupon": c.coupon_applied} for c in self.combo_items],
            "single_items": [{"name": c.item_name, "qty": c.quantity, "price": c.total_price, "coupon": c.coupon_applied} for c in self.single_items],
            "points_items": [{"name": c.item_name, "qty": c.quantity, "points": c.points_used} for c in self.points_items],
            "coupons_applied": self.coupons_applied,
            "points_used": self.points_used,
            "original_total": round(self.original_total, 2),
            "coupon_savings": round(self.coupon_savings, 2),
            "points_savings": round(self.points_savings, 2),
            "combo_savings": round(self.combo_savings, 2),
            "final_price": round(self.final_price, 2),
            "per_person_price": round(self.per_person_price, 2),
            "gaps": self.gaps,
            "constraint_satisfaction": self.constraint_satisfaction,
            "confidence": round(self.confidence, 2),
            "reasoning": self.reasoning,
            "store_id": self.store_id,
            "is_complete": self.is_complete,
            "price_status": "estimate_requires_official_quote",
            "validation_scope": "legacy_group_heuristic_not_personal_constraint_solver",
        }


# ============================================================
# Deal Composer
# ============================================================

class DealComposer:
    """Compose optimal McDonald's deals from NL requests.

    Pipeline:
    1. Parse NL → OrderSpec
    2. Fetch menu + coupons + points + mall + nutrition
    3. Categorize menu items (combos, mains, sides, drinks, etc.)
    4. Plan base allocation per constraint group
    5. Optimize: prefer combos, apply coupons, use points
    6. Score: constraint satisfaction, budget fit, savings
    7. Output: Deal with full breakdown (or closest deal with gaps)
    """

    # Category classification keywords
    COMBO_KEYWORDS = ["套餐", "combo", "超值", "组合", "meal"]
    BURGER_KEYWORDS = ["巨无霸", "汉堡", "板烧", "麦辣", "双层", "安格斯", "吉士"]
    CHICKEN_KEYWORDS = ["麦乐鸡", "炸鸡", "鸡翅", "鸡腿", "鸡块", "辣翅", "麦脆鸡"]
    VEGETARIAN_KEYWORDS = ["沙拉", "玉米", "薯条", "蔬菜", "素食", "苹果"]
    DRINK_KEYWORDS = ["可乐", "雪碧", "咖啡", "奶茶", "橙汁", "红茶", "拿铁", "饮料", "美式"]
    DESSERT_KEYWORDS = ["冰淇淋", "甜筒", "麦旋风", "派", "蛋糕", "甜品", "圣代"]
    BREAKFAST_KEYWORDS = ["早餐", "满分", "热香饼", "麦满分", "粥", "薯饼", "松饼"]

    def __init__(self, client: McpClient, config: Config):
        self.client = client
        self.config = config
        self.parser = OrderParser(client)

    async def compose_deal(
        self,
        nl_request: str,
        store_id: str = "",
        auto_apply: bool = False,
    ) -> Deal:
        """Compose a complete deal from a natural language request.

        Args:
            nl_request: Natural language like "10份饭，素食2份，汉堡加量4份，预算500"
            store_id: Target store (auto-detect if empty)
            auto_apply: If True, auto-claim coupons and apply them

        Returns:
            Deal object with full breakdown
        """
        # Step 1: Parse NL
        spec = self.parser.parse(nl_request)

        # Step 2: Fetch all data
        data = await self._fetch_all_data(store_id)
        store_id = data.get("store_id", "")

        if not data.get("menu_items"):
            return Deal(
                title="无法组合 Deal",
                description="未能获取门店菜单数据",
                order_spec=spec,
                gaps=["无法获取菜单", "请检查门店ID或网络连接"],
                confidence=0.0,
                is_complete=False,
            )

        # Step 3: Categorize menu
        categorized = self._categorize_menu(data["menu_items"])

        # Step 4: Plan base allocation
        components = self._plan_allocation(spec, categorized, data)

        # Step 5: Optimize with coupons and points
        components = self._optimize_coupons(components, data, spec)
        components = self._optimize_points(components, data, spec)

        # Step 6: Score and analyze
        deal = self._score_deal(components, spec, data, store_id)

        return deal

    async def _fetch_all_data(self, store_id: str = "") -> dict:
        """Fetch all required data from MCP in parallel-safe manner."""
        data = {
            "menu_items": [],
            "my_coupons": [],
            "available_coupons": [],
            "store_coupons": [],
            "account": None,
            "mall_products": [],
            "nutrition": [],
            "store_id": store_id,
            "store_name": "",
        }

        loc = self.config.default_location

        # Get nearest store if not specified
        if not store_id:
            stores_resp = await self.client.call_tool_safe(
                "query-nearby-stores",
                {
                    "searchType": 2,
                    "city": loc.city,
                    "keyword": loc.keyword,
                    "beType": loc.be_type,
                },
            )
            stores = ResponseParser.parse_stores(stores_resp)
            if stores:
                store_id = str(
                    stores[0].get("storeCode")
                    or stores[0].get("storeId")
                    or stores[0].get("id", "")
                )
                data["store_name"] = stores[0].get("storeName") or stores[0].get("name", "")
                data["store_id"] = store_id

        if not store_id:
            return data

        # Fetch menu (needs storeCode + beType + orderType)
        menu_resp = await self.client.call_tool_safe(
            "query-meals",
            {"storeCode": store_id, "beType": loc.be_type, "orderType": 1},
        )
        data["menu_items"] = ResponseParser.parse_menu(menu_resp)

        # Fetch coupons (my + available + store)
        my_coupons_resp = await self.client.call_tool_safe("query-my-coupons")
        data["my_coupons"] = ResponseParser.parse_coupons(my_coupons_resp)

        avail_resp = await self.client.call_tool_safe("available-coupons")
        data["available_coupons"] = ResponseParser.parse_available_coupons(avail_resp)

        store_coupons_resp = await self.client.call_tool_safe(
            "query-store-coupons",
            {"storeCode": store_id, "orderType": 1},
        )
        if isinstance(store_coupons_resp, str):
            data["store_coupons"] = ResponseParser.parse_coupons(store_coupons_resp)
        elif isinstance(store_coupons_resp, dict) and "error" not in store_coupons_resp:
            coupon_list = store_coupons_resp.get("coupons") or store_coupons_resp.get("data", {}).get("coupons", [])
            data["store_coupons"] = [
                ResponseParser._coupon_from_dict(c, "store") for c in coupon_list
                if isinstance(c, dict)
            ]

        # Fetch account
        account_resp = await self.client.call_tool_safe("query-my-account")
        data["account"] = ResponseParser.parse_account(account_resp)

        # Fetch mall products
        mall_resp = await self.client.call_tool_safe("mall-points-products")
        data["mall_products"] = ResponseParser.parse_mall_products(mall_resp)

        # Fetch nutrition
        nutrition_resp = await self.client.call_tool_safe("list-nutrition-foods")
        data["nutrition"] = ResponseParser.parse_nutrition(nutrition_resp)

        return data

    def _categorize_menu(self, items: list[ParsedMenuItem]) -> dict:
        """Categorize menu items by type."""
        categorized = {
            "combos": [],
            "burgers": [],
            "chicken": [],
            "vegetarian": [],
            "drinks": [],
            "desserts": [],
            "breakfast": [],
            "all": items,
        }

        for item in items:
            name = item.name.lower()
            assigned = False

            if self._matches_any(name, self.COMBO_KEYWORDS):
                categorized["combos"].append(item)
                assigned = True

            if self._matches_any(name, self.BREAKFAST_KEYWORDS):
                categorized["breakfast"].append(item)
                assigned = True

            if self._matches_any(name, self.BURGER_KEYWORDS):
                categorized["burgers"].append(item)
                assigned = True

            if self._matches_any(name, self.CHICKEN_KEYWORDS):
                categorized["chicken"].append(item)
                assigned = True

            if "vegetarian" in item.verified_dietary_tags:
                categorized["vegetarian"].append(item)
                assigned = True

            if self._matches_any(name, self.DRINK_KEYWORDS):
                categorized["drinks"].append(item)
                assigned = True

            if self._matches_any(name, self.DESSERT_KEYWORDS):
                categorized["desserts"].append(item)
                assigned = True

            if not assigned:
                # Unclassified items go to burgers as fallback mains
                pass

        # Sort each category by price (cheapest first for optimization)
        for key in categorized:
            if key != "all":
                categorized[key].sort(key=lambda x: x.price)

        return categorized

    def _plan_allocation(
        self,
        spec: OrderSpec,
        categorized: dict,
        data: dict,
    ) -> list[DealComponent]:
        """Plan the base item allocation for each constraint group."""
        components = []
        total_assigned = 0

        # Parse constraints into groups
        constraint_groups = self._parse_constraint_groups(spec)

        # If specific items requested, handle those first
        if spec.items:
            for item in spec.items:
                matched = self._find_item_in_menu(item.name, categorized["all"])
                if matched:
                    total = matched.price * item.quantity
                    components.append(DealComponent(
                        component_type="single",
                        item_name=matched.name,
                        product_code=matched.product_code,
                        quantity=item.quantity,
                        unit_price=matched.price,
                        total_price=total,
                        original_unit_price=matched.price,
                        original_total_price=total,
                        assigned_to="specific-request",
                    ))
                    total_assigned += item.quantity

        # Handle each constraint group
        for ctype, count in constraint_groups.items():
            category_key = self._constraint_to_category(ctype)
            available = [item for item in categorized.get(category_key, [])
                         if self._item_matches_constraint(item, ctype)]

            if not available:
                # Gap: no items found for this constraint
                for _ in range(count):
                    components.append(DealComponent(
                        component_type="gap",
                        item_name=f"[未找到{ctype}选项]",
                        assigned_to=ctype,
                        notes=f"无法满足{ctype}需求",
                    ))
                continue

            # Try to find combos that match this constraint first
            matching_combos = [
                c for c in categorized["combos"]
                if self._item_matches_constraint(c, ctype)
            ]

            for i in range(count):
                if matching_combos:
                    # Use a combo (better value)
                    combo = matching_combos[0]
                    components.append(DealComponent(
                        component_type="combo",
                        item_name=combo.name,
                        product_code=combo.product_code,
                        quantity=1,
                        unit_price=combo.price,
                        total_price=combo.price,
                        original_unit_price=combo.price,
                        original_total_price=combo.price,
                        assigned_to=ctype,
                    ))
                elif available:
                    # Use a single item
                    item = available[0]
                    components.append(DealComponent(
                        component_type="single",
                        item_name=item.name,
                        product_code=item.product_code,
                        quantity=1,
                        unit_price=item.price,
                        total_price=item.price,
                        original_unit_price=item.price,
                        original_total_price=item.price,
                        assigned_to=ctype,
                    ))
                else:
                    components.append(DealComponent(
                        component_type="gap",
                        item_name=f"[{ctype}选项不足]",
                        assigned_to=ctype,
                    ))

            total_assigned += count

        # Fill remaining people with best-value combos
        remaining = spec.total_people - total_assigned
        if remaining > 0:
            best_combos = categorized.get("combos", [])
            best_items = categorized.get("burgers", []) or categorized.get("all", [])

            pool = best_combos if best_combos else best_items
            if not pool:
                for _ in range(remaining):
                    components.append(DealComponent(
                        component_type="gap",
                        item_name="[无法分配餐品]",
                        assigned_to="default",
                    ))
            else:
                for i in range(remaining):
                    item = pool[0]
                    ctype = "combo" if item in categorized.get("combos", []) else "single"
                    components.append(DealComponent(
                        component_type=ctype,
                        item_name=item.name,
                        product_code=item.product_code,
                        quantity=1,
                        unit_price=item.price,
                        total_price=item.price,
                        original_unit_price=item.price,
                        original_total_price=item.price,
                        assigned_to="default",
                    ))

        return components

    def _optimize_coupons(
        self,
        components: list[DealComponent],
        data: dict,
        spec: OrderSpec,
    ) -> list[DealComponent]:
        """Apply best-matching coupons to components."""
        # Merge all available coupons
        components = [replace(c) for c in components]
        all_coupons = data["my_coupons"] + data["store_coupons"]

        if not all_coupons:
            return components

        # For each component, try to find a matching coupon
        used_coupon_titles = set()
        for comp in components:
            if comp.component_type in ("gap",):
                continue

            best_coupon = None
            best_savings = 0.0

            for coupon in all_coupons:
                if not coupon.coupon_id or coupon.coupon_id in used_coupon_titles:
                    continue

                # Check if coupon matches this item
                if self._coupon_matches_item(coupon, comp):
                    savings = comp.unit_price - coupon.discount_price
                    if coupon.discount_price > 0 and savings > best_savings:
                        best_savings = savings
                        best_coupon = coupon

            if best_coupon:
                original_price = comp.total_price
                comp.coupon_applied = best_coupon.title
                comp.total_price = best_coupon.discount_price * comp.quantity
                comp.unit_price = best_coupon.discount_price
                used_coupon_titles.add(best_coupon.coupon_id)

        return components

    def _optimize_points(
        self,
        components: list[DealComponent],
        data: dict,
        spec: OrderSpec,
    ) -> list[DealComponent]:
        """Consider using points to get free items."""
        components = [replace(c) for c in components]
        account = data.get("account")
        mall_products = data.get("mall_products", [])

        if not account or account.available_points <= 0 or not mall_products:
            return components

        # Find affordable mall products
        affordable = [
            p for p in mall_products
            if p.verified_redeemable and p.cash_price == 0
            and p.points_cost > 0 and p.points_cost <= account.available_points
        ]

        if not affordable:
            return components

        # Sort by points efficiency (value per point)
        affordable.sort(key=lambda p: p.cash_price / max(p.points_cost, 1), reverse=True)

        # Try to replace the cheapest single items with points-redeemed items
        remaining_points = account.available_points
        points_items = []

        # Sort components by price ascending (replace cheapest first - less value lost)
        sorted_comps = sorted(
            [c for c in components if c.component_type in ("single", "combo") and not c.coupon_applied],
            key=lambda c: c.total_price,
        )

        for comp in sorted_comps:
            if remaining_points <= 0:
                break

            # Find a matching mall product
            for product in affordable:
                if product.points_cost <= remaining_points:
                    # Check if this product matches the component's purpose
                    if comp.quantity == 1 and self._mall_matches_component(product, comp):
                        orig_price = comp.original_unit_price
                        comp.points_used = product.points_cost
                        comp.is_free = True
                        comp.total_price = 0.0
                        comp.unit_price = 0.0
                        comp.notes = f"积分兑换 (原价¥{orig_price:.2f})"
                        remaining_points -= product.points_cost
                        break

        # Add points items to components
        components.extend(points_items)

        return components

    def _score_deal(
        self,
        components: list[DealComponent],
        spec: OrderSpec,
        data: dict,
        store_id: str,
    ) -> Deal:
        """Score the deal and generate the final Deal object."""
        # Separate components by type
        combo_items = [c for c in components if c.component_type == "combo" and not c.is_free]
        single_items = [c for c in components if c.component_type == "single" and not c.is_free]
        points_items = [c for c in components if c.is_free and c.points_used > 0]
        gap_items = [c for c in components if c.component_type == "gap"]

        # Clean price calculation using preserved original prices
        original_total = 0.0
        final_price = 0.0
        coupon_savings = 0.0
        points_savings = 0.0

        for c in components:
            if c.component_type == "gap":
                continue

            # Use original_total_price as the baseline (set during planning)
            orig = c.original_total_price if c.original_total_price > 0 else (c.unit_price * c.quantity)
            original_total += orig

            if c.component_type == "points-redemption":
                # Free item via points redemption
                points_savings += c.unit_price * c.quantity  # cash value saved
                # final_price += 0 (free)

            elif c.is_free and c.points_used > 0:
                # Was a paid item, now free via points
                points_savings += orig
                # final_price += 0 (free)

            elif c.coupon_applied:
                # Coupon applied: final price is c.total_price (coupon price)
                final_price += c.total_price
                coupon_savings += orig - c.total_price

            else:
                # Normal paid item
                final_price += c.total_price

        # Calculate per-person
        per_person = final_price / spec.total_people if spec.total_people > 0 else final_price

        # Constraint satisfaction
        constraint_satisfaction = {}
        constraint_groups = self._parse_constraint_groups(spec)
        for ctype, required in constraint_groups.items():
            satisfied = sum(
                c.quantity for c in components
                if c.assigned_to == ctype and c.component_type in ("combo", "single")
            )
            constraint_satisfaction[f"{ctype}"] = f"{satisfied}/{required}"

        # Also check specific items
        if spec.items:
            for item in spec.items:
                matched = sum(
                    c.quantity for c in components
                    if item.name in c.item_name and c.component_type in ("combo", "single")
                )
                constraint_satisfaction[f"item:{item.name}"] = f"{matched}/{item.quantity}"

        # Gaps - only actual problems, not informational items
        gaps = [c.item_name for c in gap_items]
        for key, ratio in constraint_satisfaction.items():
            got, needed = map(int, ratio.split("/"))
            if got < needed:
                gaps.append(f"未满足 {key}: {ratio}")
        if sum(constraint_groups.values()) > spec.total_people:
            gaps.append("分组人数超过总人数；交叉需求请使用每人明细决策工具。")

        # Budget info (not a gap unless over budget)
        budget_note = ""
        if spec.budget:
            if final_price > spec.budget:
                gaps.append(f"超出预算 ¥{final_price - spec.budget:.2f} (预算¥{spec.budget}, 实际¥{final_price:.2f})")
            else:
                budget_note = f"预算结余 ¥{spec.budget - final_price:.2f}"

        # People count check
        allocated = sum(c.quantity for c in components if c.component_type in ("combo", "single"))
        if allocated < spec.total_people:
            gaps.append(f"人数缺口: 仅分配{allocated}人, 需求{spec.total_people}人")

        # Confidence calculation: count satisfied groups vs total groups
        num_constraint_groups = len(constraint_groups)
        num_item_requests = len(spec.items)
        total_checks = num_constraint_groups + num_item_requests + 1  # +1 for people count

        satisfied_count = 0
        for v in constraint_satisfaction.values():
            if isinstance(v, str) and "/" in v:
                s, r = v.split("/")
                if int(s) >= int(r):
                    satisfied_count += 1
        if allocated >= spec.total_people:
            satisfied_count += 1

        confidence = min(1.0, satisfied_count / max(total_checks, 1))

        # Budget penalty only when over budget
        if spec.budget and final_price > spec.budget:
            confidence *= max(0.3, 1 - (final_price - spec.budget) / spec.budget)

        # Coupons and points summary
        coupons_applied = list(set(c.coupon_applied for c in components if c.coupon_applied))
        total_points_used = sum(c.points_used for c in components if c.points_used > 0)

        # Build reasoning
        reasoning_parts = []
        reasoning_parts.append(f"从{len(data.get('menu_items', []))}个菜单项中匹配")
        if combo_items:
            reasoning_parts.append(f"选用{len(combo_items)}个套餐")
        if single_items:
            reasoning_parts.append(f"选用{len(single_items)}个单点")
        if coupons_applied:
            reasoning_parts.append(f"应用{len(coupons_applied)}张优惠券节省¥{coupon_savings:.2f}")
        if total_points_used > 0:
            reasoning_parts.append(f"使用{total_points_used}积分兑换{len(points_items)}个商品")
        if budget_note:
            reasoning_parts.append(budget_note)
        if gaps:
            reasoning_parts.append(f"存在{len(gaps)}个缺口")
        reasoning = "; ".join(reasoning_parts)

        is_complete = len(gaps) == 0

        # Title
        if is_complete:
            title = f"完整 Deal 方案 ({spec.total_people}人)"
        else:
            title = f"最接近 Deal 方案 ({spec.total_people}人, {len(gaps)}个缺口)"

        # Description
        desc_parts = []
        if spec.meal_type:
            desc_parts.append(f"餐次:{spec.meal_type}")
        if spec.budget:
            desc_parts.append(f"预算:¥{spec.budget}")
        if spec.constraints:
            desc_parts.append(f"要求:{','.join(spec.constraints)}")
        if spec.notes:
            desc_parts.append(f"场景:{spec.notes}")
        description = " | ".join(desc_parts) if desc_parts else "默认方案"

        return Deal(
            title=title,
            description=description,
            order_spec=spec,
            components=components,
            combo_items=combo_items,
            single_items=single_items,
            coupons_applied=coupons_applied,
            points_used=total_points_used,
            points_items=points_items,
            original_total=original_total,
            coupon_savings=coupon_savings,
            points_savings=points_savings,
            combo_savings=0.0,
            final_price=final_price,
            per_person_price=per_person,
            gaps=gaps,
            constraint_satisfaction=constraint_satisfaction,
            confidence=confidence,
            reasoning=reasoning,
            store_id=store_id,
            store_name=data.get("store_name", ""),
            is_complete=is_complete,
        )

    # ============================================================
    # Private helpers
    # ============================================================

    def _parse_constraint_groups(self, spec: OrderSpec) -> dict:
        """Parse OrderSpec constraints into a count map."""
        result = {}
        for c in spec.constraints:
            parts = c.split(":")
            ctype = parts[0].strip()
            count = int(parts[1]) if len(parts) > 1 else 1
            result[ctype] = result.get(ctype, 0) + count
        return result

    def _constraint_to_category(self, constraint: str) -> str:
        """Map a constraint type to a menu category."""
        mapping = {
            "vegetarian": "vegetarian",
            "extra-burger": "burgers",
            "burger": "burgers",
            "chicken": "chicken",
            "drink": "drinks",
            "no-spicy": "burgers",  # Fallback to burgers
        }
        return mapping.get(constraint, "combos")

    def _matches_any(self, text: str, keywords: list) -> bool:
        return any(kw.lower() in text for kw in keywords)

    def _find_item_in_menu(self, name: str, items: list) -> Optional[ParsedMenuItem]:
        """Find a menu item matching the requested name."""
        name_lower = name.lower()
        for item in items:
            if name_lower in item.name.lower():
                return item
        # Fuzzy match
        for item in items:
            for word in name.split():
                if word and word.lower() in item.name.lower():
                    return item
        return None

    def _combo_matches_constraint(self, combo_name: str, constraint: str) -> bool:
        """Check if a combo matches a dietary constraint."""
        name_lower = combo_name.lower()
        if constraint.startswith("vegetarian"):
            return False  # A name cannot prove ingredients or dietary compatibility.
        elif constraint.startswith("no-spicy") or constraint.startswith("extra-burger"):
            return False
        elif constraint.startswith("extra-burger") or constraint.startswith("burger"):
            return self._matches_any(name_lower, self.BURGER_KEYWORDS)
        elif constraint.startswith("chicken"):
            return self._matches_any(name_lower, self.CHICKEN_KEYWORDS)
        elif constraint.startswith("drink"):
            return self._matches_any(name_lower, self.DRINK_KEYWORDS)
        return True

    def _item_matches_constraint(self, item: ParsedMenuItem, constraint: str) -> bool:
        if constraint in ("vegetarian", "no-spicy", "extra-burger"):
            return constraint in item.verified_dietary_tags
        return self._combo_matches_constraint(item.name, constraint)

    def _coupon_matches_item(self, coupon: ParsedCoupon, comp: DealComponent) -> bool:
        """Check if a coupon can be applied to a component."""
        return bool(coupon.verified_usable and coupon.coupon_id and comp.product_code
                    and comp.product_code in coupon.verified_product_codes
                    and coupon.discount_price > 0 and comp.quantity <= coupon.max_quantity)

    def _mall_matches_component(self, product: ParsedMallProduct, comp: DealComponent) -> bool:
        """Check if a mall product can replace a component."""
        return bool(product.verified_redeemable and comp.product_code
                    and comp.product_code in product.verified_product_codes)

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
