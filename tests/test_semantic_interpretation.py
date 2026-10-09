import pytest
from pydantic import ValidationError

from mcd_assistant.matching import Intent
from test_matching import intent, setup


def meaning(**changes):
    return {'id':'sauce','person':'p1','source_quote':'不要任何调料',
            'selected_meaning':'汉堡不加酱', 'alternative_meanings':['完全不要盐和烹饪腌料'],
            'reason':'本次汉堡减脂语境，先采用常见点餐改配含义','basis':'contextual', **changes}


def case(**changes):
    return intent(transcript='我减肥，汉堡不要任何调料，热茶', participants=['p1'],
                  interpretations=[meaning()], unresolved=['官方去酱选项尚未接通'], **changes)


def test_provisional_semantics_returned_without_claiming_sauce_removed():
    matcher, cid, _, _ = setup()
    matcher.normalize({'context_id':cid,'expected_revision':0,'intent':case()})
    result = matcher.plan({'context_id':cid,'revision':1})
    assert result['plans']
    assert result['interpretations'][0]['selected_meaning'] == '汉堡不加酱'
    assert all(p['interpretations'][0]['basis']=='contextual' and not p['can_create_order'] for p in result['plans'])
    assert result['execution_blocks'] == ['官方去酱选项尚未接通']


def test_explicit_followup_supersedes_provisional_meaning_and_is_traceable():
    matcher, cid, _, _ = setup()
    matcher.normalize({'context_id':cid,'expected_revision':0,'intent':case()})
    strict = case()
    strict['transcript'] += '，我是说盐和腌料也不要'
    strict['interpretations'] = [meaning(source_quote='盐和腌料也不要', selected_meaning='完全无盐无腌料',basis='explicit',
                                         reason='后续明确修正')]
    strict['unresolved'] = ['无盐无腌料尚无官方食品证据']
    result = matcher.normalize({'context_id':cid,'expected_revision':1,'intent':strict})
    assert result['revision'] == 2
    assert '语义解释已更新' in result['changes']


def test_interpretation_requires_quote_and_person_and_safety_uncertainty():
    for interpretation in [meaning(source_quote='编造的原话'), meaning(person='别人'), meaning(safety_critical=True)]:
        value = case()
        value['interpretations'] = [interpretation]
        value['unresolved'] = []
        with pytest.raises(ValidationError):
            Intent.model_validate(value)


def test_greens_alternatives_preserve_all_vegetables_as_possible_meaning():
    value = case()
    value['transcript'] += '，我不吃青菜'
    value['interpretations'].append(meaning(id='greens',source_quote='不吃青菜',selected_meaning='不要生菜',
        alternative_meanings=['不要任何蔬菜，包括洋葱和酸黄瓜'],reason='先按汉堡配料语境理解'))
    validated = Intent.model_validate(value)
    assert len(validated.interpretations) == 2
