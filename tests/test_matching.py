import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcd_assistant.decision import InputError
from mcd_assistant.live import LiveWorkbench
from mcd_assistant.matching import Intent, Matcher, coverage, meal_variants, open_at, SHANGHAI
from mcd_assistant.weather import Weather


class FakeSupply:
    def __init__(self):
        self.calls = []
        self.rise = 0

    def __call__(self, token, name, args):
        self.calls.append((name, args))
        assert token == 'test-only'
        if name == '__discover__':
            return ['query-nearby-stores', 'query-meals', 'query-meal-detail', 'calculate-price']
        if name == 'query-nearby-stores':
            data = [{'storeCode': s, 'storeName': s, 'distance': d, 'businessStatus': True,
                     'businessStartTime': '00:00', 'businessEndTime': '00:00'} for s, d in [('s1', 150), ('s2', 750)]]
        elif name == 'query-my-account':
            data = {'availablePoint': '127.5'}
        elif name == 'query-my-coupons':
            data = {'coupons': [], 'totalCount': 0, 'totalPages': 0}
        elif name == 'campaign-calendar':
            data = {'dailyList': []}
        elif name == 'query-meals':
            data = {'meals': {'burger': {'name': '汉堡', 'currentPrice': '12'},
                              'combo': {'name': '汉堡套餐', 'currentPrice': '15'},
                              'tea': {'name': '热茶', 'currentPrice': '6'}}}
        elif name == 'query-store-coupons':
            data = [{'couponId': 'private-coupon-id', 'couponCode': 'private-coupon-code', 'title': '汉堡券',
                     'products': [{'productCode': 'burger'}]}] * 2
        elif name == 'query-meal-detail':
            data = {'code': args['code'], 'name': args['code'], 'rounds': []}
            if args['code'] == 'combo':
                data['rounds'] = [
                    {'id': 1, 'minQuantity': 1, 'maxQuantity': 1, 'choices': [
                        {'code': 'burger', 'name': '汉堡', 'quantity': 1, 'isDefault': 1, 'maxQuantity': 1}]},
                    {'id': 2, 'minQuantity': 1, 'maxQuantity': 1, 'choices': [
                        {'code': 'coke', 'name': '可乐', 'quantity': 1, 'isDefault': 1, 'maxQuantity': 1},
                        {'code': 'tea', 'name': '热茶', 'quantity': 0, 'isDefault': 0, 'maxQuantity': 1}]}]
        elif name == 'calculate-price':
            prices = {'burger': 1200, 'combo': 1500, 'tea': 600}
            products = []
            for item in args['items']:
                unit = prices[item['productCode']] - (400 if item.get('couponId') else 0)
                products.append({'productCode': item['productCode'], 'quantity': item['quantity'],
                                 'subtotal': unit * item['quantity']})
            data = {'price': sum(p['subtotal'] for p in products) + self.rise, 'productList': products, 'discount': 0}
        else:
            raise AssertionError(name)
        return {'code': 200, 'data': data}


def intent(**changes):
    return {'transcript': '一个人，一个汉堡和热茶，预算三十', 'summary': '一人汉堡和热茶，30元内',
            'people': 1, 'budget_cents': 3000,
            'requirements': [{'id': 'main', 'person': 'p1', 'description': '汉堡', 'source_quote': '汉堡', 'any_of': ['burger'], 'quantity': 1},
                             {'id': 'drink', 'person': 'p1', 'description': '热茶', 'source_quote': '热茶', 'any_of': ['tea'], 'quantity': 1}],
            **changes}


def setup():
    supply = FakeSupply()
    clock = [datetime(2026, 10, 9, 12, tzinfo=SHANGHAI).timestamp()]
    matcher = Matcher(LiveWorkbench(supply, 'test-only'), lambda _: {'status': 'forecast', 'weather_code': 61}, lambda: clock[0])
    context = matcher.context({'city': '上海', 'keyword': '测试位置'})
    for s in context['stores']:
        matcher.prepare({'context_id': context['context_id'], 'store_code': s['store_code'], 'product_codes': ['burger', 'combo', 'tea']})
    return matcher, context['context_id'], supply, clock


def test_match_combo_coupon_and_alternative_with_real_whole_cart_quotes():
    m, cid, supply, _ = setup()
    m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': intent()})
    result = m.plan({'context_id': cid, 'revision': 1, 'max_quotes': 16})
    assert len(result['plans']) == 2
    assert result['plans'][0]['cash_cents'] == 1400
    assert result['plans'][0]['coupon_count_submitted'] == 1
    assert all(p['within_budget'] for p in result['plans'])
    assert len(result['plans'][0]['allocation']) == 2
    public = json.dumps(result)
    assert 'private-coupon-id' not in public and 'private-coupon-code' not in public
    assert result['search']['global_optimal_proven'] is False
    assert all(n not in {'create-order', 'bind-coupon', 'lottery-draw'} for n, _ in supply.calls)


def test_single_food_unit_cannot_satisfy_two_people_or_two_requirements():
    value = intent(people=2, requirements=[
        {'id': f'p{i}', 'person': f'p{i}', 'description': '汉堡', 'source_quote': '汉堡', 'any_of': ['burger'], 'quantity': 1}
        for i in range(2)])
    assert coverage(Intent.model_validate(value), [{'code': 'burger', 'name': '汉堡', 'quantity': 1}])[0] is False
    assert coverage(Intent.model_validate(value), [{'code': 'burger', 'name': '汉堡', 'quantity': 2}])[0] is True


def test_matching_uses_augmenting_path_for_overlapping_demand_sets():
    value = intent(requirements=[
        {'id': 'flex', 'person': 'p1', 'description': '饮料', 'source_quote': '热茶', 'any_of': ['tea', 'coke'], 'quantity': 1},
        {'id': 'fixed', 'person': 'p1', 'description': '热茶', 'source_quote': '热茶', 'any_of': ['tea'], 'quantity': 1}])
    ok, allocation = coverage(Intent.model_validate(value), [{'code': 'tea', 'name': '茶', 'quantity': 1}, {'code': 'coke', 'name': '可乐', 'quantity': 1}])
    assert ok and next(x for x in allocation if x['requirement_id'] == 'fixed')['food_code'] == 'tea'


def test_conflicts_allow_conditional_quotes_and_revisions_invalidate_old_orders():
    m, cid, supply, _ = setup()
    m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': intent(unresolved=['不辣缺少官方依据'])})
    before = len(supply.calls)
    result = m.plan({'context_id': cid, 'revision': 1})
    assert result['status'] == 'conditional_candidates'
    assert result['plans'] and all(not p['can_create_order'] for p in result['plans'])
    assert len(supply.calls) > before
    with pytest.raises(InputError):
        m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': intent()})
    m.normalize({'context_id': cid, 'expected_revision': 1, 'intent': intent()})
    plan = m.plan({'context_id': cid, 'revision': 2})['plans'][0]
    m.normalize({'context_id': cid, 'expected_revision': 2, 'intent': intent(budget_cents=100)})
    with pytest.raises(InputError):
        m.recheck({'context_id': cid, 'plan_id': plan['plan_id']})


def test_coupon_asset_dedup_and_no_reuse():
    m, cid, _, _ = setup()
    assert len(m.coupons) == 2  # one asset per store, duplicate official rows removed
    m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': intent(
        requirements=[{'id': 'main', 'person': 'p1', 'description': '两个汉堡', 'source_quote': '汉堡', 'any_of': ['burger'], 'quantity': 2}])})
    result = m.plan({'context_id': cid, 'revision': 1})
    assert all(p['coupon_count_submitted'] <= 1 for p in result['plans'])


def test_weather_distance_time_gates_and_expiry():
    m, cid, _, clock = setup()
    assert m.context_data['stores'][0]['walking_minutes_estimate'] == 3
    context = m.context({'city': '上海', 'keyword': '测试位置', 'max_walking_minutes': 4})
    assert len(context['stores']) == 1
    clock[0] += 601
    with pytest.raises(InputError):
        m.menu({'context_id': context['context_id'], 'store_code': 's1'})
    with pytest.raises(InputError):
        m.menu({'context_id': cid, 'store_code': 's1'})


def test_unknown_and_overnight_opening_hours():
    def when(hour):
        return datetime(2026, 10, 9, hour, tzinfo=SHANGHAI)
    s = {'businessStartTime': '22:00', 'businessEndTime': '05:00'}
    assert open_at(s, when(23)) and open_at(s, when(3))
    assert not open_at(s, when(12))
    assert open_at({}, when(12)) is None


def test_official_unlimited_choice_cap_and_round_diversity():
    detail = {'code': 'meal', 'name': 'Meal', 'rounds': [
        {'id': i, 'minQuantity': 1, 'maxQuantity': 1, 'choices': [
            {'code': f'{i}-{j}', 'name': f'food {j}', 'quantity': int(j == 0),
             'isDefault': int(j == 0), 'maxQuantity': -1} for j in range(20)]}
        for i in [1, 2]]}
    variants, truncated = meal_variants(detail, 8, ['food 19'])
    assert truncated and len(variants) == 8
    assert any(v['leaves'][0]['code'] == '1-19' for v in variants)
    assert any(v['leaves'][1]['code'] == '2-19' for v in variants)
    assert all(sum(l['quantity'] for l in v['leaves']) == 2 for v in variants)


def test_budget_never_presented_as_met_and_quote_failure_not_infeasibility():
    m, cid, _, _ = setup()
    m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': intent(budget_cents=100)})
    result = m.plan({'context_id': cid, 'revision': 1})
    assert not result['plans'] and result['status'] == 'no_verified_candidate'
    assert result['minimum_quoted_extra_budget_cents'] == 1300


def test_source_quotes_and_code_hallucination_rejected():
    m, cid, _, _ = setup()
    value = intent()
    value['requirements'][0]['source_quote'] = '原文没有这个'
    with pytest.raises(ValidationError):
        m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': value})
    value = intent()
    value['requirements'][0]['any_of'] = ['fake-product']
    with pytest.raises(InputError):
        m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': value})


def test_recheck_reports_actual_price_change():
    m, cid, supply, _ = setup()
    m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': intent()})
    p = m.plan({'context_id': cid, 'revision': 1})['plans'][0]
    supply.rise = 2500
    result = m.recheck({'context_id': cid, 'plan_id': p['plan_id']})
    assert result['price_delta_cents'] == 2500 and not result['plan']['within_budget']


def test_weather_hourly_selection_unknown_and_failure():
    weather = Weather(lambda _: {'hourly': {'time': ['2026-10-09T12:00'], 'temperature_2m': [18], 'precipitation': [2], 'weather_code': [61]}})
    assert weather({})['status'] == 'unknown'
    args = {'latitude': 31.2, 'longitude': 121.4, 'target_time': '2026-10-09T12:10:00+08:00'}
    assert weather(args)['weather_code'] == 61
    assert weather({**args, 'target_time': '2026-10-10T12:00:00+08:00'})['status'] == 'unknown'
    with pytest.raises(InputError):
        weather({**args, 'latitude': float('nan')})


def test_model_neutral_stdio_protocol_and_structured_error_without_credentials():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    root = Path(__file__).resolve().parents[1]

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(root / 'run_matching_server.py')],
                                      env={'MCD_MCP_TOKEN': '', 'PYTHONDONTWRITEBYTECODE': '1'})
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 13
                assert next(t for t in tools.tools if t.name == 'mcd-match-create').annotations.read_only_hint is False
                response = await session.call_tool('mcd-match-state', {})
                assert not response.is_error and response.structured_content['revision'] == 0
                response = await session.call_tool('mcd-match-context', {'city': '上海', 'keyword': '测试'})
                assert response.is_error
    asyncio.run(asyncio.wait_for(run(), 30))
