"""Data models for McDonald's Assistant."""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class UserProfile:
    """User profile from McDonald's account."""
    raw_data: dict = field(default_factory=dict)

    @property
    def available_points(self) -> int:
        return self.raw_data.get("availablePoints", 0)

    @property
    def total_points(self) -> int:
        return self.raw_data.get("totalPoints", 0)

    @property
    def expiring_points(self) -> int:
        return self.raw_data.get("expiringPoints", 0)

    def summary(self) -> str:
        lines = ["=== 麦当劳会员信息 ==="]
        lines.append(f"  可用积分: {self.available_points}")
        lines.append(f"  累计积分: {self.total_points}")
        if self.expiring_points > 0:
            lines.append(f"  即将过期积分: {self.expiring_points} (请尽快使用!)")
        return "\n".join(lines)


@dataclass
class StoreInfo:
    """Nearby store information."""
    store_id: str = ""
    name: str = ""
    address: str = ""
    distance: str = ""
    lat: float = 0.0
    lng: float = 0.0
    business_hours: str = ""
    raw_data: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"  门店: {self.name} (ID: {self.store_id})"]
        if self.address:
            lines.append(f"  地址: {self.address}")
        if self.distance:
            lines.append(f"  距离: {self.distance}")
        if self.business_hours:
            lines.append(f"  营业时间: {self.business_hours}")
        return "\n".join(lines)


@dataclass
class MealItem:
    """A meal item in an order."""
    product_code: str = ""
    name: str = ""
    price: float = 0.0
    quantity: int = 1
    category: str = ""
    calories: int = 0
    tags: list = field(default_factory=list)

    def total_price(self) -> float:
        return self.price * self.quantity


@dataclass
class CouponInfo:
    """Coupon information."""
    coupon_id: str = ""
    title: str = ""
    description: str = ""
    discount: float = 0.0
    expiry: str = ""
    status: str = ""
    raw_data: dict = field(default_factory=dict)


@dataclass
class ActivityEvent:
    """A monitored activity event."""
    event_type: str = ""  # campaign, coupon, lottery, party
    title: str = ""
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    action_taken: str = ""  # What was done (notified, auto-claimed, etc.)
    raw_data: dict = field(default_factory=dict)


@dataclass
class OrderSpec:
    """Structured order specification parsed from natural language."""
    total_people: int = 1
    budget: Optional[float] = None
    meal_type: str = ""  # lunch, dinner, breakfast, snack
    dining_mode: str = ""  # dine-in, takeaway, delivery
    constraints: list = field(default_factory=list)  # e.g., ["vegetarian:2", "extra-burger:4"]
    items: list = field(default_factory=list)  # List of MealItem
    store_id: str = ""
    notes: str = ""

    def summary(self) -> str:
        lines = ["=== 订单需求解析 ==="]
        lines.append(f"  总人数: {self.total_people}")
        if self.budget:
            lines.append(f"  预算: ¥{self.budget}")
        if self.meal_type:
            lines.append(f"  餐次: {self.meal_type}")
        if self.dining_mode:
            lines.append(f"  就餐方式: {self.dining_mode}")
        if self.constraints:
            lines.append(f"  特殊要求: {', '.join(self.constraints)}")
        if self.items:
            lines.append("  餐品明细:")
            for item in self.items:
                lines.append(f"    - {item.name} x{item.quantity} = ¥{item.total_price():.2f}")
        total = sum(i.total_price() for i in self.items)
        if total > 0:
            lines.append(f"  预估总价: ¥{total:.2f}")
        return "\n".join(lines)


@dataclass
class Recommendation:
    """A smart recommendation result."""
    title: str = ""
    description: str = ""
    items: list = field(default_factory=list)  # List of MealItem
    total_price: float = 0.0
    discounted_price: float = 0.0
    savings: float = 0.0
    calories: int = 0
    reasoning: str = ""
    coupons_applied: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"=== {self.title} ==="]
        lines.append(f"  {self.description}")
        if self.items:
            lines.append("  推荐组合:")
            for item in self.items:
                cal_str = f" ({item.calories}kcal)" if item.calories else ""
                lines.append(f"    - {item.name} x{item.quantity} = ¥{item.total_price():.2f}{cal_str}")
        lines.append(f"  原价: ¥{self.total_price:.2f}")
        if self.discounted_price > 0:
            lines.append(f"  券后价: ¥{self.discounted_price:.2f}")
            lines.append(f"  节省: ¥{self.savings:.2f}")
        if self.calories > 0:
            lines.append(f"  总热量: {self.calories} kcal")
        if self.reasoning:
            lines.append(f"  推荐理由: {self.reasoning}")
        return "\n".join(lines)
