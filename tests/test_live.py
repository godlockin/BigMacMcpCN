import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcd_assistant.decision import InputError
from mcd_assistant.live import LiveError, LiveWorkbench, default_item
from mcd_assistant.local_http import make_http_server
from mcd_assistant.mcp_client import McpClient


class FakeOfficial:
    def __init__(self):
        self.calls = []

    def __call__(self, token, name, args):
        self.calls.append((name, args))
        assert token == 'test-only-credential'
        if name == '__discover__':
            return ['query-nearby-stores', 'query-meals', 'query-meal-detail', 'calculate-price']
        if name == 'query-nearby-stores':
            assert args == {'beType': 1, 'searchType': 2, 'city': '上海', 'keyword': '七宝地铁站'}
            data = [{'storeCode': 's1', 'storeName': 'Test', 'businessStatus': True}]
        elif name == 'query-meals':
            assert args == {'storeCode': 's1', 'orderType': 1, 'beType': 1}
            data = {'meals': {'a': {'name': 'A'}, 'b': {'name': 'B'}}}
        elif name == 'query-meal-detail':
            data = {'code': args['code'], 'name': args['code'].upper(), 'rounds': []}
        elif name == 'calculate-price':
            prices = {'a': 1390, 'b': 2500}
            products = [{'productCode': i['productCode'], 'productName': i['productCode'].upper(),
                         'quantity': i['quantity'], 'subtotal': prices[i['productCode']] * i['quantity']}
                        for i in args['items']]
            total = sum(p['subtotal'] for p in products)
            data = {'price': total - (100 if len(products) > 1 else 0), 'productList': products}
        else:
            raise AssertionError(f'Unexpected external operation {name}')
        return {'success': True, 'code': 200, 'data': data}


def ready():
    call = FakeOfficial()
    state = LiveWorkbench(call, 'test-only-credential')
    state.dispatch('connect', {})
    state.dispatch('stores', {'city': '上海', 'keyword': '七宝地铁站'})
    state.dispatch('menu', {'store_code': 's1'})
    for code in ['a', 'b']:
        state.dispatch('prepare', {'code': code})
    return state, call


def person(pid, choices):
    return {'id': pid, 'name': pid, 'allowed_options': choices, 'required_tags': []}


def test_live_change_and_official_cart_quote():
    state, call = ready()
    original = [person('p1', ['a']), person('p2', ['a']), person('p3', ['a'])]
    state.dispatch('baseline', {'participants': original, 'budget_cents': 10000})
    changed = [person('p1', ['a', 'b']), person('p2', ['b'])]
    result = state.dispatch('replan', {'participants': changed, 'budget_cents': 10000, 'locked_people': ['p1']})['result']
    assert result['affected_people'] == 1
    assert result['departed_people'] == ['p3']
    quote = state.dispatch('quote', {})
    assert quote['estimate_cents'] == 3890
    assert quote['quote']['price'] == 3790
    assert quote['difference_cents'] == -100
    assert quote['order_created'] is False
    assert {name for name, _ in call.calls} <= {'__discover__', 'query-nearby-stores', 'query-meals', 'query-meal-detail', 'calculate-price'}


def test_official_request_aggregates_quantity():
    state, call = ready()
    state.dispatch('baseline', {'participants': [person('p1', ['a']), person('p2', ['a'])], 'budget_cents': 10000})
    state.dispatch('quote', {})
    assert call.calls[-1][1]['items'] == [{'productCode': 'a', 'quantity': 2}]


def test_logout_and_location_change_clear_plans():
    state, _ = ready()
    state.dispatch('stores', {'city': '上海', 'keyword': '七宝地铁站'})
    assert not state.candidates and state.store is None
    state.dispatch('logout', {})
    assert not state.token and not state.verified and not state.stores
    with pytest.raises(LiveError):
        state.dispatch('quote', {})


def test_default_item_uses_only_explicit_defaults():
    data = {'code': 'meal', 'name': 'Meal', 'rounds': [{'id': 3, 'minQuantity': 1, 'maxQuantity': 1,
            'choices': [{'code': 'wrong', 'name': 'Wrong', 'quantity': 0, 'isDefault': 0},
                        {'code': 'right', 'name': 'Right', 'quantity': 1, 'isDefault': 1}]}]}
    item, composition, labels = default_item(data)
    assert item['roundList'] == [{'round': '3', 'comboItemList': [{'code': 'right', 'quantity': 1}]}]
    assert composition == ['right'] and labels == ['Right × 1']
    data['rounds'][0]['choices'][1]['isDefault'] = 0
    with pytest.raises(LiveError):
        default_item(data)


def test_legacy_parser_prefers_structured_content():
    response = SimpleNamespace(structured_content={'data': [1]}, content=[SimpleNamespace(text='Not JSON')])
    assert McpClient._parse_result(response) == {'data': [1]}


def test_default_flag_with_zero_quantity_is_not_selected():
    detail = {'code': 'meal', 'name': 'Meal', 'rounds': [{'id': 1, 'minQuantity': 1, 'maxQuantity': 1,
              'choices': [{'code': 'selected', 'name': 'Selected', 'quantity': 1, 'isDefault': 1},
                          {'code': 'zero', 'name': 'Zero', 'quantity': 0, 'isDefault': 1}]}]}
    item, composition, _ = default_item(detail)
    assert item['roundList'][0]['comboItemList'] == [{'code': 'selected', 'quantity': 1}]
    assert composition == ['selected']


def test_unsupported_operations_and_unverified_store_are_rejected():
    state, _ = ready()
    with pytest.raises(InputError):
        state.dispatch('create-order', {})
    with pytest.raises(InputError):
        state.dispatch('menu', {'store_code': 'arbitrary'})


def test_http_secret_and_host_gates():
    state = LiveWorkbench(FakeOfficial(), 'test-only-credential')
    page = Path(__file__).parents[1] / 'web' / 'simulator.html'
    server = make_http_server(page, {}, state.dispatch, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(url) as response:
            html = response.read().decode()
        assert 'test-only-credential' not in html
        import re
        csrf = re.search(r"const SESSION='([^']+)'", html).group(1)
        request = urllib.request.Request(url + '/api/status', data=b'{}', headers={
            'Content-Type': 'application/json', 'X-Decision-Token': csrf})
        with urllib.request.urlopen(request) as response:
            assert json.load(response) == {'connected': False, 'credential_loaded': True}
        request = urllib.request.Request(url + '/', headers={'Host': 'external.example'})
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request)
        assert denied.value.code == 403
        request = urllib.request.Request(url + '/api/connect', data=b'{}', headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request)
        assert denied.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class FakeAccount(FakeOfficial):
    def __call__(self, token, name, args):
        if name == 'query-my-account':
            return {'code': 200, 'data': {'accountId': 'private-account-id', 'availablePoint': '127.5',
                    'currentMouthExpirePoint': '8.4', 'nextMouthExpirePoint': '0'}}
        if name == 'query-my-coupons':
            page = int(args['page'])
            return {'code': 200, 'data': {'coupons': [{'id': f'private-coupon-{page}', 'code': 'private-code',
                    'title': '麦金卡专享券', 'enable': 1, 'tags': []}], 'totalCount': 2, 'totalPages': 2}}
        return super().__call__(token, name, args)


def test_account_keeps_decimal_points_and_does_not_infer_membership():
    state = LiveWorkbench(FakeAccount(), 'test-only-credential')
    state.dispatch('connect', {})
    result = state.dispatch('account', {})['account']
    assert result['points']['availablePoint'] == '127.5'
    assert result['points']['currentMouthExpirePoint'] == '8.4'
    assert result['points']['frozenPoint'] is None
    assert result['membership']['status'] == 'unknown'
    assert result['coupon_total'] == 2 and len(result['coupons']) == 2 and result['coupons_complete']
    assert 'private-' not in json.dumps(result)


def test_account_partial_failure_is_not_zero_or_no_coupons():
    class BrokenPoints(FakeAccount):
        def __call__(self, token, name, args):
            if name == 'query-my-account':
                raise LiveError('Failed')
            return super().__call__(token, name, args)
    state = LiveWorkbench(BrokenPoints(), 'test-only-credential')
    state.dispatch('connect', {})
    result = state.dispatch('account', {})['account']
    assert result['points'] is None and 'points' in result['errors']
    assert result['coupons_complete'] and result['coupon_total'] == 2


def test_account_pagination_cap_reports_partial():
    class ManyPages(FakeAccount):
        def __call__(self, token, name, args):
            data = super().__call__(token, name, args)
            if name == 'query-my-coupons':
                data['data']['totalCount'] = 6
                data['data']['totalPages'] = 6
            return data
    state = LiveWorkbench(ManyPages(), 'test-only-credential')
    state.dispatch('connect', {})
    result = state.dispatch('account', {})['account']
    assert len(result['coupons']) == 5 and not result['coupons_complete']
    assert 'coupons' in result['errors']
