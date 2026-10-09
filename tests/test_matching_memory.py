"""Synthetic account, conversation and order failure-path acceptance."""
import json

import pytest
from pydantic import ValidationError

from mcd_assistant.decision import InputError
from mcd_assistant.live import LiveWorkbench
from mcd_assistant.matching import Matcher
from mcd_assistant.preference_memory import PreferenceFact, PreferenceMemory
from test_matching import FakeSupply, intent


class OrderSupply(FakeSupply):
    def __init__(self):
        super().__init__()
        self.fail_create = False
        self.creates = 0
        self.account = 'synthetic-account'

    def __call__(self, token, name, args):
        if name == 'query-my-account':
            return {'code': 200, 'data': {'accountId': self.account, 'availablePoint': '2'}}
        if name == 'create-order':
            self.creates += 1
            if self.fail_create:
                raise TimeoutError('synthetic timeout')
            return {'code': 200, 'data': {'orderId': 'synthetic-order', 'payH5Url': 'https://example.test/pay',
                    'orderDetail': {'orderStatus': '1', 'mobilePhone': 'private-phone'}}}
        if name == 'query-order':
            return {'code': 200, 'data': {'orderId': 'synthetic-order', 'orderStatus': '2', 'mobilePhone': 'private-phone'}}
        result = super().__call__(token, name, args)
        if name == 'calculate-price':
            result['data']['takeWayList'] = [{'code': 'pickup', 'title': '到店取餐', 'subtitle': '柜台领取'}]
        return result


def ready(tmp_path, unresolved=None):
    supply = OrderSupply()
    memory = PreferenceMemory(tmp_path / 'memory.db')
    m = Matcher(LiveWorkbench(supply, 'test-only'), lambda _: {'status': 'unknown'}, memory=memory)
    context = m.context({'city': '测试城市', 'keyword': '测试地点'})
    cid = context['context_id']
    for s in context['stores']:
        m.prepare({'context_id': cid, 'store_code': s['store_code'], 'product_codes': ['burger', 'combo', 'tea']})
    m.normalize({'context_id': cid, 'expected_revision': 0, 'intent': intent(budget_cents=None, unresolved=unresolved or [])})
    result = m.plan({'context_id': cid, 'revision': 1})
    plan = result['plans'][0]
    assert plan['take_way_choices'][0]['name'] == '到店取餐'
    return m, supply, {'context_id': cid, 'plan_id': plan['plan_id'], 'authorized': True,
                       'request_key': 'request-one', 'max_cash_cents': plan['cash_cents'], 'take_way_code': 'pickup'}


def test_memory_isolation_reopen_expiry_and_deletion(tmp_path):
    now = [1000.0]
    path = tmp_path / 'memory.db'
    memory = PreferenceMemory(path, lambda: now[0])
    scope = memory.account_scope('account-a')
    fact = PreferenceFact(person='甲', key='food_like', value='茶', source_quote='喜欢茶', duration='session')
    memory.remember(scope, [fact], '喜欢茶')
    assert not memory.facts(memory.account_scope('account-b'))
    with pytest.raises(InputError):
        memory.remember(scope, [fact], '别的原话')
    memory.close()
    memory = PreferenceMemory(path, lambda: now[0])
    assert memory.account_scope('account-a') == scope
    assert len(memory.facts(scope)) == 1
    assert 'account-a' not in path.read_bytes().decode('latin1')
    assert path.stat().st_mode & 0o777 == 0o600
    now[0] += 86401
    assert not memory.facts(scope)
    memory.remember(scope, [fact], '喜欢茶')
    memory.forget(scope, [memory.facts(scope)[0]['id']])
    assert not memory.facts(scope)
    memory.close()


def test_inferred_dietary_prohibited():
    with pytest.raises(ValidationError):
        PreferenceFact(person='甲', key='dietary', value='过敏', source='inferred', confidence=.5, source_quote='不想吃')


def test_session_and_explicit_precedence(tmp_path):
    memory = PreferenceMemory(tmp_path / 'facts.db')
    scope = memory.account_scope('account')
    fact = PreferenceFact(person='甲', key='food_like', value='茶', source_quote='喜欢茶')
    memory.remember(scope, [fact], '喜欢茶', 'conversation-a')
    inferred = fact.model_copy(update={'source': 'inferred', 'confidence': .4})
    memory.remember(scope, [inferred], '喜欢茶', 'conversation-a')
    assert memory.facts(scope)[0]['source'] == 'explicit'
    session = fact.model_copy(update={'value': '汉堡', 'duration': 'session'})
    memory.remember(scope, [session], '喜欢茶', 'conversation-a')
    assert len(memory.facts(scope, 'conversation-a')) == 2
    assert len(memory.facts(scope, 'conversation-b')) == 1
    memory.close()


def test_select_create_reprice_replay_and_official_status(tmp_path):
    m, supply, args = ready(tmp_path)
    with pytest.raises(InputError):
        m.create(args)
    m.select(args)
    with pytest.raises(InputError):
        m.create({**args, 'authorized': False})
    supply.rise = 100
    with pytest.raises(InputError):
        m.create(args)
    supply.rise = 0
    receipt = m.create(args)
    assert receipt['status'] == 'pending_payment'
    assert 'private-phone' not in json.dumps(receipt)
    assert m.create(args)['duplicate_prevented']
    assert supply.creates == 1
    status = m.order_status({'request_key': args['request_key']})
    assert status['payment_verified'] and not status['payment_executed']
    scope = m.scope
    m.memory.close()
    memory = PreferenceMemory(tmp_path / 'memory.db')
    restarted = Matcher(LiveWorkbench(supply, 'test-only'), lambda _: {}, memory=memory)
    restarted.context({'city': '测试城市', 'keyword': '测试地点'})
    assert restarted.scope == scope
    assert restarted.previous_intent['budget_cents'] is None
    assert restarted.create(args)['duplicate_prevented']
    assert supply.creates == 1
    assert b'https://example.test/pay' not in (tmp_path / 'memory.db').read_bytes()
    memory.close()


def test_timeout_unknown_never_retried_even_new_key(tmp_path):
    m, supply, args = ready(tmp_path)
    m.select(args)
    supply.fail_create = True
    assert m.create(args)['status'] == 'unknown'
    assert m.create(args)['duplicate_prevented']
    with pytest.raises(InputError):
        m.create({**args, 'request_key': 'different-key'})
    assert supply.creates == 1
    assert m.order_status({'request_key': args['request_key']})['retry_create_allowed'] is False
    m.memory.close()


def test_conditional_blocks_create_and_feedback_not_payment(tmp_path):
    m, supply, args = ready(tmp_path, ['饮食硬条件尚无依据'])
    m.select(args)
    with pytest.raises(InputError):
        m.create(args)
    assert supply.creates == 0
    before = m.memory.score(m.scope, [{'person': 'p1', 'food_name': '汉堡'}], m.scenario())[0]
    m.feedback({'plan_id': args['plan_id'], 'outcome': 'rejected', 'reason': '距离远'})
    after = m.memory.score(m.scope, [{'person': 'p1', 'food_name': '汉堡'}], m.scenario())[0]
    assert before == after
    assert m.feedback({'plan_id': args['plan_id'], 'outcome': 'satisfied'})['payment_verified'] is False
    m.memory.close()


def test_six_people_depth_and_exact_meals(tmp_path):
    m, _, args = ready(tmp_path)
    value = intent(transcript='六位每人套餐', summary='六份套餐', people=6, budget_cents=None,
                   participants=[f'p{i}' for i in range(6)], requirements=[], product_requirements=[
                       {'id': f'm{i}', 'person': f'p{i}', 'description': '套餐', 'source_quote': '套餐',
                        'any_of': ['combo'], 'quantity': 1} for i in range(6)])
    m.normalize({'context_id': args['context_id'], 'expected_revision': 1, 'intent': value})
    result = m.plan({'context_id': args['context_id'], 'revision': 2, 'max_nodes': 10000})
    assert len(result['plans']) == 2
    assert all(p['combo_count'] >= 6 and len(p['meal_allocation']) == 6 for p in result['plans'])
    assert {p['store']['store_code'] for p in result['plans']} == {'s1', 's2'}
    m.memory.close()


def test_resume_old_prices_cannot_be_selected(tmp_path):
    m, _, args = ready(tmp_path)
    identity = m.conversation_id
    assert m.conversation({'operation': 'resume', 'conversation_id': identity})['last_result']['plans']
    with pytest.raises(InputError):
        m.select(args)
    m.preferences({'operation': 'clear'})
    assert m.memory.dialogue(m.scope) is None
    m.memory.close()
