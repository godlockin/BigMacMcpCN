"""Model-neutral order matching. Host interprets speech; tools validate and quote."""
from __future__ import annotations

import itertools
import math
import secrets
import sys
import json
import time
from decimal import Decimal, InvalidOperation
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .decision import InputError, integer, object_value, text
from .live import LiveError, LiveWorkbench, default_item, response_data
from .preference_memory import PreferenceMemory, PreferenceFact
from urllib.parse import urlparse
import hashlib

SHANGHAI = ZoneInfo('Asia/Shanghai')
TTL = 600


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Demand(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    person: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=300)
    source_quote: str = Field(min_length=1, max_length=500)
    any_of: list[str] = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=16)


class ProductDemand(Demand):
    """Exact top-level product request, e.g. a bucket rather than one wing."""
    pass


class Interpretation(StrictModel):
    """Host-model arbitration of ambiguous language, distinct from fulfilment evidence."""
    id: str = Field(min_length=1, max_length=64)
    person: str = Field(min_length=1, max_length=64)
    source_quote: str = Field(min_length=1, max_length=500)
    selected_meaning: str = Field(min_length=1, max_length=300)
    alternative_meanings: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=500)
    basis: Literal['contextual', 'explicit'] = 'contextual'
    safety_critical: bool = False


class Intent(StrictModel):
    transcript: str = Field(min_length=1, max_length=12000)
    summary: str = Field(min_length=1, max_length=2000)
    people: int = Field(ge=1, le=16)
    budget_cents: int | None = Field(default=None, ge=0, le=10000000)
    requirements: list[Demand] = Field(default_factory=list, max_length=32)
    product_requirements: list[ProductDemand] = Field(default_factory=list, max_length=16)
    participants: list[str] = Field(default_factory=list, max_length=16)
    assumptions: list[str] = Field(default_factory=list, max_length=32)
    interpretations: list[Interpretation] = Field(default_factory=list, max_length=32)
    excluded_codes: list[str] = Field(default_factory=list, max_length=128)
    unresolved: list[str] = Field(default_factory=list, max_length=32)
    preferences: list[str] = Field(default_factory=list, max_length=32)
    corrections: list[str] = Field(default_factory=list, max_length=32)
    priority: Literal['price', 'travel', 'coupons'] = 'price'

    @model_validator(mode='after')
    def validate_evidence(self):
        all_requirements = self.requirements + self.product_requirements
        if not all_requirements:
            raise ValueError('至少一项食品或整套商品需求')
        if len({r.id for r in all_requirements}) != len(all_requirements):
            raise ValueError('需求 ID 不能重复')
        if len({r.person for r in all_requirements if r.person != '共享'}) > self.people:
            raise ValueError('需求涉及的人数超过总人数')
        if sum(r.quantity for r in self.requirements) > 64:
            raise ValueError('一次撮合最多 64 个餐品需求单位')
        if sum(r.quantity for r in self.product_requirements) > 16:
            raise ValueError('整套商品需求一次最多 16 份')
        if self.participants and (len(self.participants) != self.people or len(set(self.participants)) != self.people):
            raise ValueError('同行者名单须与人数一致且不重复')
        if self.participants and any(r.person not in self.participants + ['共享'] for r in all_requirements):
            raise ValueError('需求分配人不在同行者名单中')
        if self.participants and not self.unresolved and set(self.participants) - {r.person for r in all_requirements}:
            raise ValueError('每位同行者须有需求；尚无合适餐品的人员应记录为未解决条件')
        for r in all_requirements:
            if r.source_quote not in self.transcript:
                raise ValueError('需求必须引用口述中的原文')
        if len({i.id for i in self.interpretations}) != len(self.interpretations):
            raise ValueError('语义解释 ID 不能重复')
        for interpretation in self.interpretations:
            if interpretation.source_quote not in self.transcript:
                raise ValueError('语义解释必须引用口述原文')
            if self.participants and interpretation.person not in self.participants:
                raise ValueError('语义解释必须属于当前参与者')
            if interpretation.safety_critical and interpretation.basis == 'contextual' and not self.unresolved:
                raise ValueError('安全关键条件的模糊解释不能作为已满足需求')
        return self


def coverage(intent: Intent, leaves: list[dict]) -> tuple[bool, list[dict]]:
    """Bipartite capacity matching: one food unit cannot satisfy two demands."""
    slots = [(r, i) for r in intent.requirements for i in range(r.quantity)]
    units = [leaf for leaf in leaves for _ in range(leaf['quantity'])]
    owners: dict[int, int] = {}

    def assign(slot: int, seen: set[int]) -> bool:
        for index, unit in enumerate(units):
            if index in seen or unit['code'] not in slots[slot][0].any_of:
                continue
            if unit.get('owner') not in {None, slots[slot][0].person, '共享'}:
                continue
            seen.add(index)
            if index not in owners or assign(owners[index], seen):
                owners[index] = slot
                return True
        return False

    complete = True
    for i in range(len(slots)):
        complete = assign(i, set()) and complete
    allocation = [{'requirement_id': slots[slot][0].id, 'person': slots[slot][0].person,
                   'food_code': units[index]['code'], 'food_name': units[index]['name']}
                  for index, slot in sorted(owners.items())]
    return complete, allocation


def allocate_cart(intent: Intent, chosen: list[dict]) -> tuple[bool, list[dict], list[dict], int]:
    roots = [(r, i) for r in intent.product_requirements for i in range(r.quantity)]
    owners: dict[int, int] = {}
    def assign(slot: int, seen: set[int]) -> bool:
        for index, v in enumerate(chosen):
            if index in seen or v['item']['productCode'] not in roots[slot][0].any_of:
                continue
            seen.add(index)
            if index not in owners or assign(owners[index], seen):
                owners[index] = slot
                return True
        return False
    roots_complete = True
    for i in range(len(roots)):
        roots_complete = assign(i, set()) and roots_complete
    leaves = [{**l, 'owner': roots[owners[index]][0].person if index in owners else None}
              for index, v in enumerate(chosen) for l in v['leaves']]
    ok, food_allocation = coverage(intent, leaves)
    if roots_complete and not ok and roots:
        # Alternate meal ownership can make leaf demands feasible. Bounded, no optimality claim.
        attempts = 0
        def alternate(slot: int, assigned: dict[int, int]) -> tuple[dict[int, int], list[dict]] | None:
            nonlocal attempts
            attempts += 1
            if attempts > 128:
                return None
            if slot == len(roots):
                foods = [{**l, 'owner': roots[assigned[i]][0].person if i in assigned else None}
                         for i, v in enumerate(chosen) for l in v['leaves']]
                complete, allocation = coverage(intent, foods)
                return (assigned, allocation) if complete else None
            for i, variant in enumerate(chosen):
                if i not in assigned and variant['item']['productCode'] in roots[slot][0].any_of:
                    result = alternate(slot + 1, {**assigned, i: slot})
                    if result:
                        return result
            return None
        alternative = alternate(0, {})
        if alternative:
            owners, food_allocation = alternative
            ok = True
    root_allocation = [{'requirement_id': roots[slot][0].id, 'person': roots[slot][0].person,
                        'product_code': chosen[index]['item']['productCode'], 'product_name': chosen[index]['name'],
                        'composition': chosen[index]['leaves']} for index, slot in sorted(owners.items())]
    return ok and roots_complete, food_allocation, root_allocation, len(food_allocation) + len(root_allocation)


def meal_variants(detail: dict, limit: int = 24, preferred_terms: list[str] | None = None) -> tuple[list[dict], bool]:
    """Enumerate legal round selections, respecting per-choice and round limits."""
    default_item(detail)  # reject incomplete defaults instead of guessing
    rounds = detail.get('rounds', [])
    if not rounds:
        return [{'item': {'productCode': detail['code'], 'quantity': 1},
                 'leaves': [{'code': detail['code'], 'name': detail['name'], 'quantity': 1}],
                 'combo': False}], False
    choices_by_round = []
    truncated = False
    for group in rounds:
        choices = sorted(group['choices'], key=lambda c: (
            not any(term in c.get('name', '') for term in (preferred_terms or [])), c.get('isDefault') != 1))
        possibilities = []
        round_max = integer(group.get('maxQuantity'), 'round max', 1, 16)
        caps = [round_max if c.get('maxQuantity') == -1 else
                integer(c.get('maxQuantity', round_max), 'choice max', 0, 16) for c in choices]
        # Official choice max=-1 means no per-choice cap; round cap still applies.
        default = tuple(integer(c.get('quantity', 0), 'default quantity', 0, 16)
                        if c.get('isDefault') == 1 else 0 for c in choices)
        def legal_counts():
            yield default
            visited = 0
            for quantity in range(integer(group.get('minQuantity', 0), 'round min', 0, 16), round_max + 1):
                for indexes in itertools.combinations_with_replacement(range(len(choices)), quantity):
                    visited += 1
                    if visited > 4096:
                        return
                    count = Counter(indexes)
                    yield tuple(count[i] for i in range(len(choices)))
        candidates = legal_counts()
        seen = set()
        for counts in candidates:
            if counts in seen:
                continue
            seen.add(counts)
            if not group.get('minQuantity', 0) <= sum(counts) <= round_max or any(q > cap for q, cap in zip(counts, caps)):
                continue
            possibilities.append([(c, q) for c, q in zip(choices, counts) if q])
            if len(possibilities) >= limit:
                truncated = True
                break
        if len(possibilities) >= limit:
            truncated = True
        if not possibilities:
            raise LiveError('选配轮次无合法组成')
        choices_by_round.append(possibilities)
    result = []
    base = tuple(options[0] for options in choices_by_round)
    singles = []
    for alternative in range(1, limit):
        for i, options in enumerate(choices_by_round):
            if alternative < len(options):
                singles.append(tuple(options[alternative] if j == i else base[j] for j in range(len(base))))
    selections = itertools.chain([base], singles, itertools.product(*choices_by_round))
    seen_selections = set()
    for selection in selections:
        key = tuple(tuple((c['code'], q) for c, q in chosen) for chosen in selection)
        if key in seen_selections:
            continue
        seen_selections.add(key)
        if len(result) == limit:
            truncated = True
            break
        round_list, leaves = [], []
        for group, chosen in zip(rounds, selection):
            round_list.append({'round': str(group['id']), 'comboItemList': [
                {'code': c['code'], 'quantity': q} for c, q in chosen]})
            leaves.extend({'code': c['code'], 'name': c['name'], 'quantity': q} for c, q in chosen)
        result.append({'item': {'productCode': detail['code'], 'quantity': 1, 'roundList': round_list},
                       'leaves': leaves, 'combo': True})
    return result, truncated


def open_at(store: dict, when: datetime) -> bool | None:
    try:
        start = datetime.strptime(store['businessStartTime'], '%H:%M').time()
        end = datetime.strptime(store['businessEndTime'], '%H:%M').time()
    except (KeyError, ValueError, TypeError):
        return None
    local = when.astimezone(SHANGHAI).time().replace(tzinfo=None)
    if start == end:
        return True
    return start <= local < end if start < end else local >= start or local < end


class Matcher:
    def __init__(self, live: LiveWorkbench, weather: Callable[[dict], dict], clock: Callable[[], float] = time.time,
                 memory: PreferenceMemory | None = None):
        self.live, self.weather, self.clock = live, weather, clock
        self.context_data: dict | None = None
        self.catalog: dict[str, list[dict]] = {}
        self.coupons: dict[str, dict] = {}
        self.intent: Intent | None = None
        self.revision = 0
        self.plans: dict[str, dict] = {}
        self.catalog_truncated = False
        self.memory = memory or PreferenceMemory(clock=clock)
        self.scope: str | None = None
        self.conversation_id = secrets.token_hex(12)
        self.previous_intent: dict | None = None
        self.last_result: dict | None = None
        self.selected_plan: str | None = None

    def clear(self) -> None:
        self.context_data = None
        self.catalog, self.coupons, self.plans = {}, {}, {}
        self.intent, self.revision = None, 0
        self.catalog_truncated = False
        self.scope = None
        self.conversation_id = secrets.token_hex(12)
        self.previous_intent = None
        self.last_result = None
        self.selected_plan = None

    def context(self, payload: object) -> dict:
        row = object_value(payload, 'context')
        city, keyword = text(row.get('city'), 'city'), text(row.get('keyword'), 'keyword')
        now = datetime.fromtimestamp(self.clock(), SHANGHAI)
        target = row.get('target_time')
        when = datetime.fromisoformat(str(target)) if target else now
        if when.tzinfo is None:
            when = when.replace(tzinfo=SHANGHAI)
        when = when.astimezone(SHANGHAI)
        if when < now - timedelta(minutes=2) or when > now + timedelta(days=1):
            raise InputError('目标时间须为现在至未来 24 小时（北京时间）')
        max_distance = integer(row.get('max_distance_m', 3000), 'max distance', 0, 50000)
        max_walk = integer(row['max_walking_minutes'], 'walking minutes', 1, 240) if 'max_walking_minutes' in row else None
        if not self.live.verified:
            self.live.dispatch('connect', {})
        stores = self.live.dispatch('stores', {'city': city, 'keyword': keyword})['stores']
        account = self.live.dispatch('account', {})['account']
        new_scope = self.memory.account_scope(self.live.account_identity) if self.live.account_identity else None
        old_scope, conversation, old_revision = self.scope, self.conversation_id, self.revision
        previous = self.intent.model_dump() if self.intent else self.previous_intent
        previous_result = self.last_result
        self.clear()
        self.scope = new_scope
        if new_scope and new_scope == old_scope:
            self.conversation_id, self.revision, self.previous_intent = conversation, old_revision, previous
            self.last_result = previous_result
        elif new_scope:
            resumed = self.memory.dialogue(new_scope)
            if resumed:
                self.conversation_id, self.revision = resumed['conversation_id'], resumed['revision']
                self.previous_intent = resumed.get('intent')
                self.last_result = resumed.get('last_result')
        weather_request = object_value(row.get('weather', {}), 'weather request')
        forecast = self.weather({**weather_request, 'target_time': when.isoformat()})
        codes = forecast.get('weather_code')
        wet = isinstance(codes, int) and codes >= 51
        weather_factor = 1.35 if wet else 1.0
        accepted, rejected = [], []
        for s in stores:
            distance = s.get('distance')
            reason = None
            if s.get('businessStatus') is not True and not (target and when > now + timedelta(minutes=2)):
                reason = '当前暂停营业，无法承诺预约可售'
            elif isinstance(distance, (int, float)) and distance > max_distance:
                reason = '超过最大距离'
            elif open_at(s, when) is False:
                reason = '目标时间不在营业时段'
            if reason:
                rejected.append({'store_name': s.get('storeName'), 'reason': reason})
                continue
            travel = math.ceil(distance / 75 * weather_factor) if isinstance(distance, (int, float)) and distance >= 0 else None
            if max_walk is not None and (travel is None or travel > max_walk):
                rejected.append({'store_name': s.get('storeName'), 'reason': '步行估算超过上限或距离未知'})
                continue
            accepted.append({'store_code': s['storeCode'], 'store_name': s.get('storeName'),
                             'address': s.get('address'), 'distance_m': distance,
                             'walking_minutes_estimate': travel, 'open_at_target': open_at(s, when),
                             'walking_weather_factor': weather_factor,
                             'leave_by_estimate': (when - timedelta(minutes=travel)).isoformat() if travel is not None and target else None,
                             'reservation_slots': s.get('reservationTimeOptions', []),
                             'opening_hours': f"{s.get('businessStartTime', '?')}–{s.get('businessEndTime', '?')}"})
        activities, errors = [], []
        try:
            data = object_value(response_data(self.live.invoke('campaign-calendar', {
                'specifiedDate': when.strftime('%Y-%m-%d')})), 'campaign')
            for day in data.get('dailyList', []):
                if day.get('date') == when.strftime('%Y-%m-%d'):
                    activities.extend({'title': e.get('activityTitle'), 'subtitle': e.get('activitySubTitle'),
                                       'status': 'information_only_not_discount'} for e in day.get('events', []))
        except (LiveError, InputError, TypeError, ValueError):
            errors.append('活动查询失败，不代表没有活动')
        accepted.sort(key=lambda s: s['distance_m'] if isinstance(s['distance_m'], (int, float)) else math.inf)
        self.context_data = {'context_id': secrets.token_hex(8), 'created': self.clock(),
                             'city': city, 'keyword': keyword, 'target_time': when.isoformat(),
                             'reservation': bool(target), 'stores': accepted[:3], 'rejected_stores': rejected,
                             'weekday': when.isoweekday(), 'conversation_id': self.conversation_id,
                             'revision': self.revision,
                             'memory': self.memory_view(),
                             'max_walking_minutes': max_walk,
                             'store_search_truncated': len(accepted) > 3, 'account': account,
                             'weather': forecast, 'activities': activities, 'errors': errors,
                             'limits': ['步行按 75 米/分钟估算；雨雪系数 1.35，非导航或出餐预测',
                                        '接口距离以搜索关键词为参照，不是设备 GPS；天气坐标由用户指定',
                                        '只搜索前 3 家合适门店；营业时间未知时需确认',
                                        '活动日历只作信息；核价折扣不归因于未经证实的会员或活动']}
        return self.public_context()

    def public_context(self) -> dict:
        return {**{k: v for k, v in self.context_data.items() if k != 'created'},
                'conversation_id': self.conversation_id, 'revision': self.revision,
                'memory': self.memory_view()} if self.context_data else {}

    def check_context(self, context_id: str) -> dict:
        if self.context_data is None or context_id != self.context_data['context_id']:
            raise InputError('位置／账号上下文已变化，请重新读取')
        if self.clock() - self.context_data['created'] > TTL:
            raise InputError('上下文已超过 10 分钟，请重新查询菜单与权益')
        if self.context_data['reservation'] and datetime.fromisoformat(self.context_data['target_time']).timestamp() <= self.clock():
            raise InputError('预约时点已过去，请更新取餐时间')
        return self.context_data

    def args(self, store_code: str) -> dict:
        context = self.context_data
        args = {'storeCode': store_code, 'orderType': 1, 'beType': 1}
        if context and context['reservation']:
            args['reservationDate'] = datetime.fromisoformat(context['target_time']).strftime('%Y-%m-%d %H:%M')
        return args

    def menu(self, payload: object) -> dict:
        row = object_value(payload, 'menu request')
        context = self.check_context(text(row.get('context_id'), 'context id'))
        code = text(row.get('store_code'), 'store code')
        if code not in {s['store_code'] for s in context['stores']}:
            raise InputError('门店不在当前撮合范围')
        data = object_value(response_data(self.live.invoke('query-meals', self.args(code))), 'menu')
        meals = object_value(data.get('meals'), 'meals')
        return {'context_id': context['context_id'], 'store_code': code, 'meals': [
            {'code': k, 'name': v.get('name'), 'reference_price_yuan': v.get('currentPrice'),
             'discount_label': v.get('discountType'), 'requires_card_purchase': bool(v.get('withOrder'))}
            for k, v in meals.items()], 'categories': data.get('categories', []),
            'note': '名称不证明忌口或营养属性；价格为参考，不能直接相加作结算价'}

    def prepare(self, payload: object) -> dict:
        row = object_value(payload, 'catalog request')
        menu = self.menu(row)
        code = menu['store_code']
        selected = row.get('product_codes')
        preferred_terms = row.get('preferred_terms', [])
        if not isinstance(preferred_terms, list) or len(preferred_terms) > 16 or any(not isinstance(t, str) or not 1 <= len(t) <= 64 for t in preferred_terms):
            raise InputError('偏好词最多 16 个，每个 1–64 字符')
        if not isinstance(selected, list) or not 1 <= len(selected) <= 8 or len(set(selected)) != len(selected):
            raise InputError('每家门店选 1–8 个不重复商品编码')
        if not set(selected) <= {m['code'] for m in menu['meals']}:
            raise InputError('商品必须来自当前官方菜单')
        errors, variants = [], []
        coupon_rows = []
        try:
            coupon_rows = response_data(self.live.invoke('query-store-coupons', self.args(code)))
            if not isinstance(coupon_rows, list):
                raise LiveError('门店券结构不匹配')
        except (LiveError, InputError):
            errors.append('门店可用券读取失败；不代表无券')
        self.coupons = {k: v for k, v in self.coupons.items() if v['store_code'] != code}
        seen_coupon_ids = set()
        for coupon in coupon_rows:
            if not isinstance(coupon, dict) or not coupon.get('couponId') or not coupon.get('couponCode'):
                continue
            if coupon['couponId'] in seen_coupon_ids:
                continue
            seen_coupon_ids.add(coupon['couponId'])
            alias = secrets.token_hex(6)
            self.coupons[alias] = {**coupon, 'store_code': code}
        for product in selected:
            try:
                detail = object_value(response_data(self.live.invoke('query-meal-detail', {
                    **self.args(code), 'code': product})), 'detail')
                if detail.get('code') != product:
                    raise LiveError('商品编码不匹配')
                generated, truncated = meal_variants(detail, 6, preferred_terms)
                reference = next(m['reference_price_yuan'] for m in menu['meals'] if m['code'] == product)
                try:
                    reference_cents = max(0, int(Decimal(str(reference)) * 100))
                except (InvalidOperation, ValueError, TypeError, OverflowError):
                    reference_cents = 10000000
                self.catalog_truncated |= truncated
                for variant in generated:
                    alias = secrets.token_hex(6)
                    variants.append({**variant, 'id': alias, 'name': detail['name'],
                                     'store_code': code, 'coupon_alias': None, 'coupon_title': None,
                                     'reference_cents': reference_cents,
                                     'support_modify': detail.get('supportModify') is True})
                    for coupon_alias, coupon in self.coupons.items():
                        if coupon['store_code'] == code and product in {p.get('productCode') for p in coupon.get('products', [])}:
                            variants.append({**variant, 'id': secrets.token_hex(6), 'name': detail['name'],
                                             'store_code': code, 'coupon_alias': coupon_alias,
                                             'reference_cents': reference_cents,
                                             'coupon_title': coupon.get('title'),
                                             'support_modify': detail.get('supportModify') is True})
                            if len(variants) >= 48:
                                break
                    if len(variants) >= 48:
                        self.catalog_truncated = True
                        break
            except (LiveError, InputError, TypeError, KeyError, ValueError):
                errors.append(f'商品 {product} 详情不可用，已排除')
            if len(variants) >= 48:
                break
        self.catalog[code] = variants[:48]
        # Old host intent may refer to removed child codes; force a fresh versioned intent.
        self.intent = None
        self.plans = {}
        self.selected_plan = None
        return {'context_id': menu['context_id'], 'store_code': code,
                'variants': [self.public_variant(v) for v in self.catalog[code]],
                'errors': errors, 'truncated': self.catalog_truncated,
                'note': '选配来自真实轮次；券是门店候选权益，须整单核价。未改特调、未买卡。'}

    @staticmethod
    def public_variant(v: dict) -> dict:
        return {'product_code': v['item']['productCode'],
                **{k: v[k] for k in ['id', 'name', 'store_code', 'leaves', 'combo', 'coupon_title', 'support_modify']}}

    def normalize(self, payload: object) -> dict:
        row = object_value(payload, 'intent request')
        self.check_context(text(row.get('context_id'), 'context id'))
        if integer(row.get('expected_revision', 0), 'revision', 0) != self.revision:
            raise InputError('需求版本冲突，请先读取当前状态')
        intent = Intent.model_validate(row.get('intent'))
        known = {leaf['code'] for variants in self.catalog.values() for v in variants for leaf in v['leaves']}
        products = {v['item']['productCode'] for variants in self.catalog.values() for v in variants}
        if not known:
            raise InputError('先准备真实候选，再将口述映射到餐品编码')
        if not set(intent.excluded_codes) <= known or any(not set(r.any_of) <= known for r in intent.requirements):
            raise InputError('需求编码不在当前真实餐品组成中，不接受虚构编码')
        if any(not set(r.any_of) <= products for r in intent.product_requirements):
            raise InputError('整套商品需求必须来自当前真实顶层商品编码')
        old = self.intent.model_dump() if self.intent else self.previous_intent
        self.intent = intent
        self.revision += 1
        self.plans = {}
        self.previous_intent = intent.model_dump()
        self.last_result = None
        self.persist_dialogue()
        return {'revision': self.revision, 'intent': intent.model_dump(),
                'conversation_id': self.conversation_id, 'status': 'conditional' if intent.unresolved else 'ready',
                'changes': self.intent_changes(old, intent.model_dump()),
                'note': '由宿主模型理解口述；工具校验原文引用、编码和版本，不冒充自动语义审查'}

    def quote_items(self, variants: list[dict]) -> list[dict]:
        counts = Counter(v['id'] for v in variants)
        items = []
        for identity, quantity in counts.items():
            v = next(v for v in variants if v['id'] == identity)
            item = {**v['item'], 'quantity': quantity}
            if v['coupon_alias']:
                if quantity != 1:
                    raise InputError('同一券资产不可重复使用')
                coupon = self.coupons[v['coupon_alias']]
                item.update(couponId=coupon['couponId'], couponCode=coupon['couponCode'])
            items.append(item)
        return items

    def official_quote(self, store_code: str, variants: list[dict]) -> dict:
        items = self.quote_items(variants)
        quote = object_value(response_data(self.live.invoke('calculate-price', {
            **self.args(store_code), 'items': items, 'needTableware': False})), 'quote')
        expected = Counter()
        returned = Counter()
        for item in items:
            expected[item['productCode']] += item['quantity']
        for row in quote.get('productList', []):
            returned[text(row.get('productCode'), 'quoted code')] += integer(row.get('quantity'), 'quoted quantity', 1, 128)
        if expected != returned:
            raise LiveError('官方核价商品数量与方案不一致')
        integer(quote.get('price'), 'official price')
        return quote

    @staticmethod
    def intent_changes(before: dict | None, after: dict) -> list[str]:
        if before is None:
            return ['首次需求']
        changes = []
        for key, label in [('people','人数'),('participants','同行者'),('budget_cents','预算'),
                           ('priority','优先目标'),('requirements','食品需求'),('product_requirements','整套商品需求'),
                           ('excluded_codes','禁止餐品'),('unresolved','硬条件'),('assumptions','默认仲裁'),
                           ('interpretations','语义解释')]:
            if before.get(key) != after.get(key):
                changes.append(label + '已更新')
        return changes or ['需求未改变']

    def scenario(self) -> dict:
        context = self.context_data or {}
        weather = context.get('weather', {})
        code = weather.get('weather_code')
        label = 'wet' if isinstance(code, int) and code >= 51 else 'dry' if weather.get('status') == 'forecast' else 'unknown'
        participants = self.intent.participants if self.intent and self.intent.participants else sorted({
            r.person for r in (self.intent.requirements + self.intent.product_requirements if self.intent else []) if r.person != '共享'})
        return {'date_time': context.get('target_time'), 'weekday': context.get('weekday'),
                'weather': label, 'companions': sorted(p.casefold() for p in participants),
                'city': context.get('city'), 'location': context.get('keyword')}

    def memory_view(self) -> dict:
        if not self.scope or not self.live.account_identity or self.memory.account_scope(self.live.account_identity) != self.scope:
            return {'status': 'unavailable', 'facts': [], 'history': [], 'note': '账号身份未验证，不读取或写入其他账号记忆'}
        return {'status': 'local_account_scoped', 'facts': self.memory.facts(self.scope, self.conversation_id),
                'history': self.memory.history(self.scope),
                'note': '本机记忆；明确偏好、本次条件、推断倾向分开，选择或付款不等于满意'}

    def persist_dialogue(self) -> None:
        if self.scope:
            self.memory.save_dialogue(self.scope, self.conversation_id, self.revision,
                {'intent': self.intent.model_dump() if self.intent else self.previous_intent,
                 'scenario': self.scenario(), 'last_result': self.last_result,
                 'note': '历史报价仅作记录；恢复后须重查菜单、重新撮合和核价'})

    def search_carts(self, intent: Intent, max_nodes: int) -> tuple[list[tuple], int, bool]:
        """Bounded beam search reaches larger meals without exhausting shallow enumeration."""
        carts, nodes, truncated = [], 0, True  # beam search never proves global optimality
        maximum = min(sum(r.quantity for r in intent.requirements + intent.product_requirements), 16)
        for store_code, catalog in self.catalog.items():
            store_nodes = 0
            store_limit = max(1, max_nodes // max(1, len(self.catalog)))
            useful = [v for v in catalog if not any(l['code'] in intent.excluded_codes for l in v['leaves']) and (
                any(l['code'] in r.any_of for l in v['leaves'] for r in intent.requirements) or
                any(v['item']['productCode'] in r.any_of for r in intent.product_requirements))]
            beam = [()]
            seen = set()
            for depth in range(1, maximum + 1):
                next_beam = []
                for previous in beam:
                    for index in range(len(useful)):
                        indexes = tuple(sorted(previous + (index,)))
                        if indexes in seen:
                            continue
                        seen.add(indexes)
                        if nodes >= max_nodes:
                            return carts, nodes, truncated
                        if store_nodes >= store_limit:
                            break
                        nodes += 1
                        store_nodes += 1
                        chosen = [useful[i] for i in indexes]
                        coupons = [v['coupon_alias'] for v in chosen if v['coupon_alias']]
                        if len(set(coupons)) != len(coupons):
                            continue
                        complete, allocation, roots, progress = allocate_cart(intent, chosen)
                        leaf_count = sum(l['quantity'] for v in chosen for l in v['leaves'])
                        root_count = sum(l['quantity'] for a in roots for l in a['composition'])
                        excess = max(0, leaf_count - len(allocation) - root_count)
                        reference = sum(v['reference_cents'] for v in chosen)
                        if complete:
                            carts.append((excess, depth, -len(coupons), store_code, chosen, allocation, roots))
                            # Also expand complete carts: additional items may activate a discount.
                        next_beam.append((-progress, excess, reference, -len(coupons), indexes))
                if not next_beam:
                    break
                beam = [entry[-1] for entry in sorted(next_beam)[:32]]
        return carts, nodes, truncated

    def plan(self, payload: object) -> dict:
        row = object_value(payload, 'plan request')
        context = self.check_context(text(row.get('context_id'), 'context id'))
        known = {leaf['code'] for vs in self.catalog.values() for v in vs for leaf in v['leaves']}
        if not self.intent or integer(row.get('revision'), 'revision', 1) != self.revision:
            raise InputError('请提交当前版本的规范化需求')
        intent = self.intent
        if any(not set(r.any_of) <= known for r in intent.requirements):
            raise InputError('需求餐品组成已变化，请重新映射')
        products = {v['item']['productCode'] for vs in self.catalog.values() for v in vs}
        if any(not set(r.any_of) <= products for r in intent.product_requirements):
            raise InputError('整套商品组成已变化，请重新映射')
        max_quotes = integer(row.get('max_quotes', 12), 'max_quotes', 1, 24)
        max_nodes = integer(row.get('max_nodes', 20000), 'max_nodes', 1, 100000)
        self.plans = {}
        survivors, failed = [], 0
        candidate_carts, nodes, truncated = self.search_carts(intent, max_nodes)
        # Diversity across stores, then round-robin official quotes; prices are never invented.
        grouped = {}
        for cart in sorted(candidate_carts, key=lambda c: (sum(v['reference_cents'] for v in c[4]), c[:3])):
            grouped.setdefault((cart[3], bool(cart[2])), []).append(cart)
        ordered = []
        while grouped and len(ordered) < max_quotes:
            for code in list(grouped):
                ordered.append(grouped[code].pop(0))
                if not grouped[code]:
                    del grouped[code]
                if len(ordered) == max_quotes:
                    break
        truncated |= len(candidate_carts) > len(ordered)
        for excess, size, _, store_code, chosen, allocation, roots in ordered:
            try:
                quote = self.official_quote(store_code, chosen)
            except (LiveError, InputError, ValueError, TypeError):
                failed += 1
                continue
            store = next(s for s in context['stores'] if s['store_code'] == store_code)
            coupons = [v['coupon_title'] for v in chosen if v['coupon_alias']]
            score, memory_reasons = self.memory.score(self.scope, allocation + [
                {'person': a['person'], 'food_name': a['product_name']} for a in roots], self.scenario(), self.conversation_id) if self.scope else (0, [])
            p = {'plan_id': secrets.token_hex(8), 'revision': self.revision, 'store': store,
                 'conversation_id': self.conversation_id,
                 'cash_cents': quote['price'], 'within_budget': intent.budget_cents is None or quote['price'] <= intent.budget_cents,
                 'budget_cents': intent.budget_cents, 'conditional': bool(intent.unresolved),
                 'execution_blocks': intent.unresolved, 'assumptions': intent.assumptions,
                 'interpretations': [i.model_dump() for i in intent.interpretations],
                 'can_create_order': not intent.unresolved and store['open_at_target'] is True,
                 'combo_count': sum(v['combo'] for v in chosen), 'single_count': sum(not v['combo'] for v in chosen),
                 'coupon_count_submitted': len(coupons), 'coupon_titles_submitted': coupons,
                 'coupon_application_status': 'official_cart_accepted_not_individual_redemption_proof',
                 'membership_applied': 'unknown', 'activity_count': None, 'points_spent': 0,
                 'discount_cents': quote.get('discount'), 'allocation': allocation, 'meal_allocation': roots,
                 'take_way_choices': self.take_way_choices(quote),
                 'memory_fit_score': score, 'memory_reasons': memory_reasons,
                 'items': [self.public_variant(v) for v in chosen], 'extra_food_units': excess,
                 'captured_at': datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat(),
                 'preferences_pending': intent.preferences, 'order_created': False,
                 'variants_private': chosen}
            self.plans[p['plan_id']] = p
            survivors.append(p)
        feasible = [p for p in survivors if p['within_budget']]

        def rank(p):
            travel = p['store']['walking_minutes_estimate']
            travel = travel if travel is not None else math.inf
            if intent.priority == 'travel':
                return travel, p['cash_cents'], -p['memory_fit_score'], p['extra_food_units']
            if intent.priority == 'coupons':
                return -p['coupon_count_submitted'], p['cash_cents'], -p['memory_fit_score'], travel
            return p['cash_cents'], -p['memory_fit_score'], travel, p['extra_food_units']

        feasible.sort(key=rank)
        selected = []
        if feasible:
            selected.append(feasible[0])
            a = feasible[0]
            alternatives = [p for p in feasible[1:] if p['store']['store_code'] != a['store']['store_code']
                            or Counter(l['food_code'] for l in p['allocation']) != Counter(l['food_code'] for l in a['allocation'])
                            or Counter(l['product_code'] for l in p['meal_allocation']) != Counter(l['product_code'] for l in a['meal_allocation'])]
            if alternatives:
                selected.append(min(alternatives, key=lambda p: (p['store']['walking_minutes_estimate']
                    if p['store']['walking_minutes_estimate'] is not None else math.inf, p['cash_cents'])))
        for i, p in enumerate(selected):
            p['reasons'] = [f"真实需求容量匹配：{len(p['allocation'])} 个食品单位、{len(p['meal_allocation'])} 份指定商品",
                            '官方整单报价，预算内' if intent.budget_cents is not None else '官方整单报价；用户未设预算',
                            {'price': '现金优先', 'travel': '步行估算优先', 'coupons': '提交券数量优先'}[intent.priority],
                            *p['memory_reasons']]
            p['tradeoff'] = {'cash_delta_cents': p['cash_cents'] - selected[0]['cash_cents'],
                            'walking_delta_minutes': (p['store']['walking_minutes_estimate'] - selected[0]['store']['walking_minutes_estimate'])
                            if p['store']['walking_minutes_estimate'] is not None and selected[0]['store']['walking_minutes_estimate'] is not None else None}
        result = {'status': ('conditional_candidates' if intent.unresolved else 'quoted_candidates') if selected else 'no_verified_candidate',
                'revision': self.revision, 'summary': intent.summary,
                'conversation_id': self.conversation_id, 'assumptions': intent.assumptions,
                'interpretations': [i.model_dump() for i in intent.interpretations],
                'execution_blocks': intent.unresolved, 'scenario': self.scenario(),
                'plans': [self.public_plan(p) for p in selected],
                'plan_b_unavailable': len(selected) < 2,
                'minimum_quoted_extra_budget_cents': min((p['cash_cents'] - intent.budget_cents for p in survivors), default=None) if not selected and intent.budget_cents is not None else None,
                'search': {'nodes': min(nodes, max_nodes), 'quoted_carts': len(ordered), 'failed_quotes': failed,
                           'truncated': truncated or self.catalog_truncated or context['store_search_truncated'],
                           'global_optimal_proven': False},
                'limits': context['limits'] + ['候选内择优，整单核价上限；不证明全菜单最低价',
                    '口述理解与饮食证据审核由宿主完成；未确认的硬性条件须放入 unresolved',
                    '报价接受用券请求不证明逐券折扣，会员／活动归因未知；偏好未自动量化'],
                'context': self.public_context()}
        self.last_result = {k: v for k, v in result.items() if k != 'context'}
        self.persist_dialogue()
        return result

    @staticmethod
    def public_plan(plan: dict) -> dict:
        return {k: v for k, v in plan.items() if k != 'variants_private'}

    @staticmethod
    def take_way_choices(quote: dict) -> list[dict]:
        ways = quote.get('takeWayList')
        if not isinstance(ways, list):
            return []
        return [{'code': w['code'], 'name': w.get('title', w.get('name', '')), 'subtitle': w.get('subtitle', '')}
                for w in ways if isinstance(w, dict) and isinstance(w.get('code'), str)]

    def recheck(self, payload: object) -> dict:
        row = object_value(payload, 'recheck')
        self.check_context(text(row.get('context_id'), 'context id'))
        identity = text(row.get('plan_id'), 'plan id')
        if identity not in self.plans or self.plans[identity]['revision'] != self.revision:
            raise InputError('订单已随需求变化失效，请重新撮合')
        old = self.plans[identity]
        quote = self.official_quote(old['store']['store_code'], old['variants_private'])
        new = {**old, 'cash_cents': quote['price'], 'within_budget': self.intent.budget_cents is None or quote['price'] <= self.intent.budget_cents,
               'take_way_choices': self.take_way_choices(quote),
               'captured_at': datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat()}
        self.plans[identity] = new
        return {'plan': self.public_plan(new), 'price_delta_cents': quote['price'] - old['cash_cents'],
                'order_created': False}

    def require_scope(self) -> str:
        if not self.scope or not self.live.account_identity or self.memory.account_scope(self.live.account_identity) != self.scope:
            raise InputError('请先读取已验证账号上下文；无法确认账号时禁用持久化与创建订单')
        return self.scope

    def preferences(self, payload: object) -> dict:
        row = object_value(payload, 'memory request')
        scope = self.require_scope()
        operation = row.get('operation', 'read')
        if operation == 'remember':
            facts = row.get('facts', [])
            if not isinstance(facts, list) or not 1 <= len(facts) <= 32:
                raise InputError('一次记录 1–32 项偏好')
            evidence = (self.intent.model_dump() if self.intent else self.previous_intent or {}).get('transcript', '')
            self.memory.remember(scope, [PreferenceFact.model_validate(f) for f in facts], evidence, self.conversation_id)
        elif operation == 'forget':
            identities = row.get('fact_ids', [])
            if not isinstance(identities, list) or len(identities) > 32 or any(not isinstance(i, str) for i in identities):
                raise InputError('fact_ids 必须为至多 32 个字符串')
            self.memory.forget(scope, identities)
        elif operation == 'clear':
            self.memory.clear_profile(scope)
            self.intent, self.previous_intent, self.last_result = None, None, None
            self.plans, self.selected_plan = {}, None
        elif operation != 'read':
            raise InputError('未知记忆操作')
        return self.memory_view()

    def conversation(self, payload: object) -> dict:
        row = object_value(payload, 'conversation request')
        scope = self.require_scope()
        operation = row.get('operation', 'read')
        if operation == 'new':
            self.conversation_id = secrets.token_hex(12)
            self.intent, self.previous_intent, self.last_result = None, None, None
            self.revision = 0
            self.plans, self.selected_plan = {}, None
        elif operation == 'resume':
            saved = self.memory.dialogue(scope, text(row.get('conversation_id'), 'conversation id'))
            if not saved:
                raise InputError('当前账号没有该会话')
            self.conversation_id, self.revision = saved['conversation_id'], saved['revision']
            self.previous_intent, self.last_result = saved.get('intent'), saved.get('last_result')
            self.intent, self.plans, self.selected_plan = None, {}, None
        elif operation != 'read':
            raise InputError('未知会话操作')
        return {'conversation_id': self.conversation_id, 'revision': self.revision,
                'previous_intent': self.previous_intent, 'last_result': self.last_result,
                'historical_prices_executable': False}

    def current_plan(self, payload: object) -> dict:
        row = object_value(payload, 'plan request')
        self.check_context(text(row.get('context_id'), 'context id'))
        identity = text(row.get('plan_id'), 'plan id')
        plan = self.plans.get(identity)
        if not self.intent or not plan or plan['revision'] != self.revision:
            raise InputError('当前方案已失效，请重新撮合')
        return plan

    def select(self, payload: object) -> dict:
        scope = self.require_scope()
        plan = self.current_plan(payload)
        if self.selected_plan != plan['plan_id']:
            self.memory.feedback(scope, self.conversation_id, plan, 'selected', '', self.scenario())
        self.selected_plan = plan['plan_id']
        return {'selected_plan_id': self.selected_plan, 'cash_cents': plan['cash_cents'],
                'can_create_order': plan['can_create_order'], 'order_created': False}

    def feedback(self, payload: object) -> dict:
        row = object_value(payload, 'feedback request')
        scope = self.require_scope()
        identity = text(row.get('plan_id'), 'plan id')
        plan = self.plans.get(identity)
        if not plan and self.last_result:
            plan = next((p for p in self.last_result.get('plans', []) if p['plan_id'] == identity), None)
        outcome = row.get('outcome')
        if not plan or outcome not in {'rejected', 'satisfied', 'disliked', 'cancelled'}:
            raise InputError('请选择当前或恢复会话的方案，反馈 rejected/satisfied/disliked/cancelled')
        reason = row.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 500:
            raise InputError('反馈原因至多 500 字')
        self.memory.feedback(scope, self.conversation_id, plan, outcome, reason,
                             self.last_result.get('scenario', self.scenario()) if self.last_result else self.scenario())
        return {'recorded': True, 'outcome': outcome, 'payment_verified': False}

    @staticmethod
    def receipt_status(data: dict) -> tuple[str, str]:
        code = str(data.get('orderStatus', data.get('status', 'unknown')))
        return code, {'1': 'pending_payment', '2': 'paid_preparing', '10': 'paid_preparing',
                      '4': 'delivering', '6': 'completed', '7': 'cancelled', '8': 'reviewed'}.get(code, 'unknown')

    def create(self, payload: object) -> dict:
        row = object_value(payload, 'create request')
        scope = self.require_scope()
        key = text(row.get('request_key'), 'request key')
        if len(key) > 128 or row.get('authorized') is not True:
            raise InputError('创建订单需要用户明确下单授权与稳定请求键')
        # Read durable receipt before validating ephemeral plan IDs: restart never retries a remote create.
        try:
            previous = self.memory.order_attempt(scope, key)
        except InputError:
            previous = None
        if previous:
            return {'status': previous['status'], 'order_id': previous['order_id'], 'request_key': key,
                    'duplicate_prevented': True, 'payment_executed': False}
        plan = self.current_plan(row)
        if self.selected_plan != plan['plan_id'] or not plan['can_create_order']:
            raise InputError('先选中当前方案；未解决硬性条件或营业状态未知时禁止创建')
        maximum = integer(row.get('max_cash_cents'), 'maximum cash', 0, 1000000)
        quote = self.official_quote(plan['store']['store_code'], plan['variants_private'])
        if quote['price'] > maximum or (self.intent.budget_cents is not None and quote['price'] > self.intent.budget_cents):
            raise InputError('重核价超过用户授权金额或预算，请重新选择')
        ways = quote.get('takeWayList', [])
        if not isinstance(ways, list):
            raise LiveError('官方未返回有效取餐方式')
        way = text(row.get('take_way_code'), 'take way code')
        if not any(isinstance(w, dict) and w.get('code') == way for w in ways):
            raise InputError('取餐方式必须来自最新官方核价')
        args = {**self.args(plan['store']['store_code']), 'items': self.quote_items(plan['variants_private']),
                'needTableware': False, 'takeWayCode': way}
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        _, fresh = self.memory.begin_order(scope, key, self.conversation_id, self.revision, digest)
        if not fresh:
            raise InputError('该请求已有创建记录，请查询状态')
        try:
            data = object_value(response_data(self.live.invoke('create-order', args)), 'create receipt')
            order_id = text(data.get('orderId'), 'official order id')
        except Exception:
            self.memory.finish_order(scope, key, 'unknown', None)
            return {'status': 'unknown', 'request_key': key, 'retry_create_allowed': False,
                    'message': '创建结果未知，不重试；请在官方账号查看订单', 'payment_executed': False}
        self.memory.finish_order(scope, key, 'created', order_id)
        code, status = self.receipt_status(data.get('orderDetail', {}) if isinstance(data.get('orderDetail'), dict) else {})
        result = {'status': status, 'official_status': code, 'order_id': order_id, 'request_key': key,
                  'cash_cents_quoted': quote['price'], 'payment_executed': False}
        url = data.get('payH5Url')
        if isinstance(url, str) and urlparse(url).scheme == 'https' and urlparse(url).hostname:
            result['payment_url'] = url  # returned only, never stored in dialogue or logs
        return result

    def order_status(self, payload: object) -> dict:
        row = object_value(payload, 'order status')
        scope = self.require_scope()
        key = text(row.get('request_key'), 'request key')
        attempt = self.memory.order_attempt(scope, key)
        if not attempt['order_id']:
            return {'status': attempt['status'], 'retry_create_allowed': False,
                    'message': '没有可查询订单 ID；请在官方账号核对，不自动重试创建'}
        data = object_value(response_data(self.live.invoke('query-order', {'orderId': attempt['order_id']})), 'order status')
        if data.get('orderId') != attempt['order_id']:
            raise LiveError('官方返回订单 ID 不一致')
        code, status = self.receipt_status(data)
        journal_status = 'paid' if status in {'paid_preparing', 'delivering', 'completed', 'reviewed'} else 'created'
        self.memory.finish_order(scope, key, journal_status, attempt['order_id'])
        return {'order_id': attempt['order_id'], 'status': status, 'official_status': code,
                'payment_verified': journal_status == 'paid', 'payment_executed': False}

    def dispatch(self, action: str, payload: object) -> dict:
        started = time.monotonic()
        ok = False
        try:
            result = self._dispatch(action, payload)
            ok = True
            return result
        finally:
            # No parameters, account data, coupon IDs, transcript or credentials.
            sys.stderr.write(json.dumps({'event': 'matching_tool', 'action': action,
                'success': ok, 'duration_ms': round((time.monotonic() - started) * 1000)}) + '\n')

    def _dispatch(self, action: str, payload: object) -> dict:
        methods = {'match-context': self.context, 'match-menu': self.menu, 'match-prepare': self.prepare,
                   'match-intent': self.normalize, 'match-plan': self.plan, 'match-recheck': self.recheck,
                   'match-memory': self.preferences, 'match-conversation': self.conversation,
                   'match-feedback': self.feedback, 'match-select': self.select,
                   'match-create': self.create, 'match-order-status': self.order_status}
        if action == 'match-state':
            return {'context': self.public_context(), 'revision': self.revision,
                    'conversation_id': self.conversation_id, 'previous_intent': self.previous_intent,
                    'last_result': self.last_result, 'memory': self.memory_view(), 'selected_plan_id': self.selected_plan,
                    'intent': self.intent.model_dump() if self.intent else None,
                    'catalog': {k: [self.public_variant(v) for v in vs] for k, vs in self.catalog.items()}}
        if action not in methods:
            raise InputError('Unknown matching action')
        return methods[action](payload)
