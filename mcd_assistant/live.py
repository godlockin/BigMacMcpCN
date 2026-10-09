"""Explicit read-only official MCP bridge and verified default-meal adapter."""
import asyncio
import json
from datetime import datetime, timezone
from typing import Callable

from .decision import InputError, integer, object_value, solve, text


class LiveError(RuntimeError):
    pass


def response_data(value: object) -> object:
    row = object_value(value, 'official response')
    if row.get('success') is False or row.get('code', 200) != 200:
        raise LiveError('官方接口未成功，请刷新重试或检查账号权限。')
    if 'data' not in row:
        raise LiveError('官方响应缺少 data，未猜测数据格式。')
    return row['data']


def default_item(detail: object) -> tuple[dict, list[str], list[str]]:
    """Use explicit default quantities; never substitute arbitrary first choices."""
    row = object_value(detail, 'meal detail')
    code = text(row.get('code'), 'meal code')
    item = {'productCode': code, 'quantity': 1}
    composition, labels, rounds = [], [], []
    for raw_round in row.get('rounds', []):
        group = object_value(raw_round, 'round')
        chosen = []
        for raw in group.get('choices', []):
            choice = object_value(raw, 'choice')
            if choice.get('isDefault') != 1:
                continue
            count = integer(choice.get('quantity'), 'default quantity', 0, 32)
            if count == 0:
                continue
            child_code = text(choice.get('code'), 'choice code')
            chosen.append({'code': child_code, 'quantity': count})
            composition.extend([child_code] * count)
            label = text(choice.get('name'), 'choice name')
            labels.append(f"{label}{'【可特调】' if choice.get('supportModify') is True else ''} × {count}")
        quantity = sum(c['quantity'] for c in chosen)
        if not group.get('minQuantity', 0) <= quantity <= group.get('maxQuantity', quantity):
            raise LiveError('套餐默认组成不完整，请选择其他候选。')
        if not chosen:
            raise LiveError('套餐没有明确默认选项，暂不自动生成。')
        rounds.append({'round': str(group['id']), 'comboItemList': chosen})
    if rounds:
        item['roundList'] = rounds
    else:
        composition = [code]
        labels = [text(row.get('name'), 'meal name')]
    return item, composition, labels


class OfficialTransport:
    """Each operation owns its session; no cross-loop async resource reuse."""
    def __call__(self, token: str, name: str, arguments: dict) -> object:
        async def request():
            from mcp import ClientSession
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
            client = create_mcp_http_client(headers={'Authorization': 'Bearer ' + token})
            async with client:
                async with streamable_http_client('https://mcp.mcd.cn', http_client=client) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=30) as session:
                        await session.initialize()
                        if name == '__discover__':
                            result = await session.list_tools()
                            return [t.name for t in result.tools]
                        result = await session.call_tool(name, arguments)
                        if result.is_error:
                            raise LiveError('官方工具返回错误，请检查输入或稍后重试。')
                        if result.structured_content is not None:
                            return result.structured_content
                        return json.loads('\n'.join(b.text for b in result.content if hasattr(b, 'text')))
        try:
            return asyncio.run(asyncio.wait_for(request(), timeout=45))
        except Exception:
            # Remote exception bodies can carry credentials/account data.
            raise LiveError('连接或查询失败，请检查 Token、网络和服务状态。') from None


class LiveWorkbench:
    def __init__(self, call: Callable[[str, str, dict], object], token: str = ''):
        self.call = call
        self.token = token
        self.verified = False
        self.account_identity: str | None = None
        self.stores: dict[str, dict] = {}
        self.store: dict | None = None
        self.menu: dict[str, dict] = {}
        self.candidates: dict[str, dict] = {}
        self.baseline: list[dict] = []
        self.result: dict | None = None

    def reset_plan(self) -> None:
        self.store = None
        self.menu = {}
        self.candidates = {}
        self.baseline = []
        self.result = None

    def invoke(self, name: str, args: dict) -> object:
        if not self.token:
            raise LiveError('请先连接本人 Token。')
        return self.call(self.token, name, args)

    def context(self) -> dict:
        if self.store is None:
            raise InputError('请先选择门店。')
        return {'storeCode': self.store['storeCode'], 'orderType': 1, 'beType': 1}

    def dispatch(self, action: str, payload: object) -> dict:
        request = object_value(payload, 'request')
        if action == 'status':
            return {'connected': self.verified, 'credential_loaded': bool(self.token)}
        if action == 'connect':
            supplied = request.get('token')
            token = text(supplied, 'Token') if supplied else self.token
            if not token:
                raise InputError('请输入 Token。')
            tools = self.call(token, '__discover__', {})
            if not isinstance(tools, list) or not {'query-nearby-stores', 'query-meals', 'query-meal-detail', 'calculate-price'} <= set(tools):
                raise LiveError('服务缺少必需工具。')
            self.token, self.verified = token, True
            self.account_identity = None
            self.stores = {}
            self.reset_plan()
            return {'connected': True, 'tool_count': len(tools)}
        if action == 'logout':
            self.token, self.verified = '', False
            self.account_identity = None
            self.stores = {}
            self.reset_plan()
            return {'connected': False}
        if not self.verified:
            raise LiveError('请先验证账号连接。')
        if action == 'account':
            profile: dict = {
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'membership': {'status': 'unknown', 'message': '当前官方工具未提供麦金卡持卡状态。券名称、菜单优惠或卡包为空都不能证明是否持卡。'},
                'points': None, 'coupons': [], 'coupon_total': None,
                'coupons_complete': False, 'errors': {}}
            try:
                data = object_value(response_data(self.invoke('query-my-account', {})), 'account')
                identity = data.get('accountId')
                self.account_identity = identity if isinstance(identity, str) and identity else None
                fields = ['availablePoint', 'accumulativePoint', 'frozenPoint',
                          'currentMouthExpirePoint', 'nextMouthExpirePoint', 'usedPoint', 'expiredPoint']
                # Points can be decimal strings; never round to integer or assume missing = 0.
                profile['points'] = {k: str(data[k]) if data.get(k) is not None else None for k in fields}
            except (LiveError, InputError, ValueError, TypeError):
                self.account_identity = None
                profile['errors']['points'] = '积分读取失败；未将失败或缺失数据显示为 0。'
            try:
                total_pages = 1
                seen: set[str] = set()
                for page in range(1, 6):
                    data = object_value(response_data(self.invoke('query-my-coupons', {
                        'page': str(page), 'pageSize': '200'})), 'coupons')
                    total_pages = integer(data.get('totalPages'), 'totalPages', 0, 100000)
                    profile['coupon_total'] = integer(data.get('totalCount'), 'totalCount', 0, 100000)
                    rows = data.get('coupons')
                    if not isinstance(rows, list):
                        raise LiveError('卡包格式不匹配。')
                    for raw in rows:
                        coupon = object_value(raw, 'coupon')
                        identity = text(coupon.get('id'), 'coupon id')
                        if identity in seen:
                            continue
                        seen.add(identity)
                        tags = coupon.get('tags', [])
                        profile['coupons'].append({
                            'title': text(coupon.get('title'), 'coupon title'),
                            'subtitle': str(coupon.get('subtitle', '')),
                            'enable': coupon.get('enable') == 1,
                            'time': str(coupon.get('datetimeText', '')),
                            'start_date': str(coupon.get('tradeStartDate', '')),
                            'end_date': str(coupon.get('tradeEndDate', '')),
                            'tags': [str(t.get('label', '')) for t in tags if isinstance(t, dict)] if isinstance(tags, list) else []})
                    if page >= total_pages:
                        profile['coupons_complete'] = len(seen) == profile['coupon_total']
                        break
                if not profile['coupons_complete']:
                    profile['errors']['coupons'] = '卡包未完整读取（最多查询 5 页），当前列表仅为部分结果。'
            except (LiveError, InputError, ValueError, TypeError):
                profile['errors']['coupons'] = '卡包读取失败或不完整，不代表账号没有优惠券。'
            return {'account': profile}
        if action == 'stores':
            data = response_data(self.invoke('query-nearby-stores', {
                'beType': 1, 'searchType': 2,
                'city': text(request.get('city'), '城市'),
                'keyword': text(request.get('keyword'), '位置关键词')}))
            if not isinstance(data, list):
                raise LiveError('门店响应格式不匹配。')
            stores = [object_value(s, 'store') for s in data]
            self.stores = {text(s.get('storeCode'), 'store code'): s for s in stores}
            self.reset_plan()
            return {'stores': stores}
        if action == 'menu':
            code = text(request.get('store_code'), 'store code')
            if code not in self.stores:
                raise InputError('请从当前搜索结果选择门店。')
            store = self.stores[code]
            if store.get('businessStatus') is not True:
                raise InputError('当前门店暂停营业。')
            data = object_value(response_data(self.invoke('query-meals', {
                'storeCode': code, 'orderType': 1, 'beType': 1})), 'menu')
            meals = object_value(data.get('meals'), 'menu.meals')
            self.reset_plan()
            self.store, self.menu = store, meals
            return {'store': store, 'meals': [{'code': k, **object_value(v, 'meal')} for k, v in meals.items()]}
        if action == 'prepare':
            code = text(request.get('code'), 'meal code')
            if code not in self.menu:
                raise InputError('请从当前菜单选择餐品。')
            if len(self.candidates) >= 16 and code not in self.candidates:
                raise InputError('本机实时工作台最多保留 16 个候选。')
            detail = object_value(response_data(self.invoke('query-meal-detail', {**self.context(), 'code': code})), 'detail')
            if detail.get('code') != code:
                raise LiveError('餐品详情编码不匹配。')
            item, composition, labels = default_item(detail)
            quote = object_value(response_data(self.invoke('calculate-price', {
                **self.context(), 'items': [item], 'needTableware': False})), 'quote')
            products = quote.get('productList')
            if not isinstance(products, list) or len(products) != 1 or products[0].get('productCode') != code or products[0].get('quantity') != 1:
                raise LiveError('官方单餐核价结果与请求不一致。')
            option = {'id': code, 'name': text(detail.get('name'), 'meal name'),
                      'price_cents': integer(products[0].get('subtotal'), 'subtotal'),
                      'items': composition, 'verified_tags': [], 'points': 0, 'resources': []}
            prepared = {'option': option, 'official_item': item, 'composition': labels,
                        'support_modify': detail.get('supportModify') is True,
                        'captured_at': datetime.now(timezone.utc).isoformat(),
                        'quote_total_cents': integer(quote.get('price'), 'price')}
            self.candidates[code] = prepared
            self.result = None
            return {'candidate': prepared, 'candidate_count': len(self.candidates)}
        if action in {'baseline', 'replan'}:
            self.context()
            if not self.candidates:
                raise InputError('请先添加完整餐候选，并自行确认适合一人用餐。')
            if action == 'replan' and not self.baseline:
                raise InputError('请先保存原方案。')
            data = {'snapshot': {'source': 'user_snapshot', 'captured_at': min(c['captured_at'] for c in self.candidates.values()),
                                 'store_id': self.store['storeCode'],
                                 'options': [c['option'] for c in self.candidates.values()], 'resources': {}, 'points_budget': 0},
                    'participants': request.get('participants'), 'budget_cents': request.get('budget_cents'),
                    'previous_assignments': self.baseline if action == 'replan' else [],
                    'locked_people': request.get('locked_people', []) if action == 'replan' else []}
            result = solve(data)
            self.result = result
            if action == 'baseline' and result['assignments']:
                self.baseline = result['assignments']
            return {'result': result, 'baseline_saved': bool(self.baseline)}
        if action == 'quote':
            if self.result is None or not self.result['assignments']:
                raise InputError('请先计算有效方案。')
            quantities: dict[str, int] = {}
            for assignment in self.result['assignments']:
                code = assignment['option_id']
                quantities[code] = quantities.get(code, 0) + 1
            items = [{**self.candidates[code]['official_item'], 'quantity': q} for code, q in quantities.items()]
            quote = object_value(response_data(self.invoke('calculate-price', {
                **self.context(), 'items': items, 'needTableware': False})), 'quote')
            products = quote.get('productList')
            if not isinstance(products, list):
                raise LiveError('整单核价缺少商品明细。')
            returned: dict[str, int] = {}
            for product in products:
                row = object_value(product, 'quoted product')
                code = text(row.get('productCode'), 'quoted code')
                returned[code] = returned.get(code, 0) + integer(row.get('quantity'), 'quoted quantity', 1, 16)
            if returned != quantities:
                raise LiveError('官方核价商品与方案不一致，未将此结果作为有效报价。')
            total = integer(quote.get('price'), 'official price')
            return {'quote': quote, 'estimate_cents': self.result['total_cents'],
                    'difference_cents': total - self.result['total_cents'],
                    'captured_at': datetime.now(timezone.utc).isoformat(), 'order_created': False}
        raise InputError('Unknown action')
