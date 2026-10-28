import json
from pathlib import Path

from app.rules import OCRLine
from app.upgrade_materials import detect_upgrade_materials, filter_upgrade_materials, is_upgrade_material


SHAPE=(900,1600,3)


def line(text,left=.86,top=.56,right=.98,bottom=.60,score=.85):
    return OCRLine(text,score,[[left*1600,top*900],[right*1600,top*900],
                              [right*1600,bottom*900],[left*1600,bottom*900]])


def menu():
    return [line('武器',.43,.03,.48,.065),line('影之武装',.51,.03,.58,.065),
            line('回退',.84,.93,.90,.97)]


def candidate(name,kind='text',**extra):
    return {'name':name,'kind':kind,'text':name,**extra}


def test_actual_rollback_layout_excludes_both_balances():
    lines=menu()+[line('溶剂 +8'),line('铸造金属+16',.88,.66,.98,.70)]
    detected=detect_upgrade_materials(lines,SHAPE)
    assert [e['name'] for e in detected]==['溶剂','铸造金属']
    assert all(e['evidence']=='upgrade_material' for e in detected)
    assert filter_upgrade_materials([candidate('溶剂 +8'),candidate('铸造金属+16','item')],lines,SHAPE)==[]


def test_world_pickups_are_not_excluded_by_material_names():
    lines=[line('溶剂 +8'),line('铸造金属+16')]
    items=[candidate('溶剂 +8'),candidate('铸造金属+16','item')]
    assert filter_upgrade_materials(items,lines,SHAPE)==items


def test_missing_top_tab_or_bottom_rollback_does_not_match():
    for controls in (menu()[:2],menu()[1:],menu()[::2]):
        assert detect_upgrade_materials(controls+[line('精炼晶石+4')],SHAPE)==[]


def test_same_words_in_dialogue_do_not_establish_upgrade_context():
    lines=[line('武器',.3,.7,.4,.73),line('影之武装',.4,.7,.6,.73),
           line('回退',.5,.8,.6,.84),line('精炼晶石+4')]
    assert detect_upgrade_materials(lines,SHAPE)==[]


def test_weapon_title_other_categories_and_manual_marks_survive():
    lines=menu()+[line('精炼晶石+4')]
    kept=[candidate('痴者残剑',evidence='inventory_selection'),
          candidate('精炼晶石','item',evidence='item_panel'),
          candidate('精炼晶石','quest'),candidate('精炼晶石',source='manual')]
    assert filter_upgrade_materials([*kept,candidate('精炼晶石+4')],lines,SHAPE)==kept


def test_signed_spend_and_explicit_acquisition_form_are_also_material_changes():
    lines=menu()+[line('消耗品甲 −3'),line('材料乙＋8',.8,.7,.95,.73)]
    # A material's name may contain 消耗; labels starting with this word are
    # conservatively excluded from detection. The second balance still matches.
    evidence=detect_upgrade_materials(lines,SHAPE)
    assert [e['name'] for e in evidence]==['材料乙']
    assert is_upgrade_material(candidate('材料乙','item',text='获得：材料乙 +8'),evidence)
    spend=detect_upgrade_materials(menu()+[line('精炼晶石 -3')],SHAPE)
    assert is_upgrade_material(candidate('精炼晶石 -3'),spend)


def test_rank_badge_stat_increase_and_non_delta_cost_table_do_not_match():
    lines=menu()+[line('痴者残剑 +5',.67,.20,.82,.24),line('伤害能力 +33',.67,.27,.85,.30),
                  line('铸造金属 10 6',.67,.73,.94,.76)]
    assert detect_upgrade_materials(lines,SHAPE)==[]


def test_bad_or_absent_boxes_and_low_confidence_are_ignored():
    assert detect_upgrade_materials([OCRLine('武器'),OCRLine('影之武装'),OCRLine('回退'),OCRLine('材料甲+5')],SHAPE)==[]
    assert detect_upgrade_materials(menu()+[line('材料甲+5',score=.3)],SHAPE)==[]
    assert detect_upgrade_materials(menu()+[line('材料甲+5')],(0,0))==[]


def test_real_saved_ocr_regression_when_available():
    evidence_file=Path(__file__).resolve().parents[1]/'data'/'demo-audit'/'upgrade-refunds'/'frame-1086-ocr.json'
    if not evidence_file.exists(): return
    sample=json.loads(evidence_file.read_text(encoding='utf-8'))
    lines=[OCRLine(**row) for row in sample['lines']]
    evidence=detect_upgrade_materials(lines,sample['shape'])
    assert {e['name'] for e in evidence}=={'溶剂','铸造金属'}
