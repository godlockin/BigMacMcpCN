"""Regression cases independently reproduced during the repository review."""
from mcd_assistant.config import Config
from mcd_assistant.deal_composer import DealComposer, DealComponent
from mcd_assistant.models import OrderSpec, MealItem
from mcd_assistant.response_parser import ParsedMenuItem, ParsedCoupon, ParsedAccount, ParsedMallProduct


class OfflineClient:
    async def call_tool_safe(self, *args, **kwargs):
        raise AssertionError("offline regression must not call remote APIs")


def composer():
    return DealComposer(OfflineClient(), Config())


def test_two_cheap_meals_fit_budget():
    c = composer()
    spec = OrderSpec(total_people=2, budget=20)
    menu = [ParsedMenuItem(name="基础套餐", price=10), ParsedMenuItem(name="升级套餐", price=20)]
    parts = c._plan_allocation(spec, c._categorize_menu(menu), {})
    assert c._score_deal(parts, spec, {}, "test").final_price == 20


def test_names_cannot_establish_dietary_safety():
    c = composer()
    for constraint, name in [("vegetarian:1", "培根蛋套餐"), ("no-spicy:1", "麦辣鸡腿汉堡套餐")]:
        spec = OrderSpec(total_people=1, constraints=[constraint])
        menu = [ParsedMenuItem(name=name, price=20), ParsedMenuItem(name="玉米杯", price=6)]
        parts = c._plan_allocation(spec, c._categorize_menu(menu), {})
        result = c._score_deal(parts, spec, {}, "test")
        assert result.gaps and result.confidence < 1


def test_quantity_counted_not_row_count():
    c = composer()
    spec = OrderSpec(total_people=5, items=[MealItem(name="巨无霸", quantity=5)])
    menu = [ParsedMenuItem(name="巨无霸", price=20)]
    parts = c._plan_allocation(spec, c._categorize_menu(menu), {})
    result = c._score_deal(parts, spec, {}, "test")
    assert not result.gaps and result.constraint_satisfaction["item:巨无霸"] == "5/5"


def test_points_counted_once_no_paid_redemption_assumed_free():
    c = composer()
    original = DealComponent(component_type="single", product_code="corn", item_name="玉米杯",
                             unit_price=10, total_price=10, original_unit_price=10, original_total_price=10)
    data = {"account": ParsedAccount(available_points=100), "mall_products": [ParsedMallProduct(
        name="玉米杯", points_cost=100, cash_price=0, verified_product_codes=("corn",), verified_redeemable=True)]}
    parts = c._optimize_points([original], data, OrderSpec())
    result = c._score_deal(parts, OrderSpec(), data, "test")
    assert result.points_used == 100 and result.original_total == 10
    assert result.final_price == 0 and original.total_price == 10
    data["mall_products"][0].cash_price = 3
    assert c._optimize_points([original], data, OrderSpec())[0].total_price == 10


def test_coupon_needs_verified_sku_and_quantity_and_instance():
    c = composer()
    item = DealComponent(item_name="麦辣鸡腿汉堡", product_code="chicken", quantity=2, unit_price=20)
    coupon = ParsedCoupon(title="巨无霸优惠券", coupon_id="c1", discount_price=10)
    assert not c._coupon_matches_item(coupon, item)
    coupon.verified_usable = True
    coupon.verified_product_codes = ("chicken",)
    assert not c._coupon_matches_item(coupon, item)
    item.quantity = 1
    assert c._coupon_matches_item(coupon, item)
