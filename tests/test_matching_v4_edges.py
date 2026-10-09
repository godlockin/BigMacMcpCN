import pytest

from mcd_assistant.decision import InputError
from mcd_assistant.matching import Intent, allocate_cart
from mcd_assistant.preference_memory import PreferenceMemory
from test_matching import intent, setup


def test_alternate_meal_ownership_respects_persons_leaf_demands():
    value = Intent.model_validate(intent(transcript='甲乙要套餐，甲要茶，乙要可乐', people=2, budget_cents=None,
        requirements=[{'id': 'leaf', 'person': '甲', 'description': '茶', 'source_quote': '茶', 'any_of': ['tea'], 'quantity': 1}],
        product_requirements=[{'id': str(i), 'person': p, 'description': '套餐', 'source_quote': '套餐',
                               'any_of': ['combo'], 'quantity': 1} for i,p in enumerate(['甲','乙'])]))
    chosen = [{'item': {'productCode': 'combo'}, 'name': '套餐', 'leaves': [{'code': c, 'name': c, 'quantity': 1}]} for c in ['tea','coke']]
    complete, allocation, roots, _ = allocate_cart(value, chosen)
    assert complete and allocation[0]['person'] == '甲'
    assert next(r for r in roots if r['person'] == '甲')['composition'][0]['code'] == 'tea'


def test_future_store_closed_now_not_filtered_for_open_reservation():
    m, _, supply, clock = setup()
    original = supply.__class__.__call__
    class ClosedNow(supply.__class__):
        def __call__(self, token, name, args):
            result = original(self, token, name, args)
            if name == 'query-nearby-stores':
                for s in result['data']:
                    s['businessStatus'] = False
                    s['businessStartTime'], s['businessEndTime'] = '07:00', '23:00'
            return result
    m.live.call = ClosedNow()
    result = m.context({'city': '测试', 'keyword': '测试', 'target_time': '2026-10-10T11:00:00+08:00'})
    assert result['stores'] and all(s['open_at_target'] for s in result['stores'])


def test_order_journal_concurrent_writers_prevent_second_remote_claim(tmp_path):
    path = tmp_path / 'orders.db'
    first, second = PreferenceMemory(path), PreferenceMemory(path)
    scope = first.account_scope('synthetic')
    first.begin_order(scope, 'one', 'dialogue', 1, 'cart')
    with pytest.raises(InputError):
        second.begin_order(scope, 'two', 'dialogue', 2, 'cart')
    first.close()
    second.close()
