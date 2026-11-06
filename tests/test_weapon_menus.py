"""Menu weapons must not consume a first pickup or produce OCR variants."""
from unittest.mock import Mock

import numpy as np
import pytest

from app.collection_refinement import refine_collection_lines
from app.collection_taxonomy import detect_collection_candidates, FirstCollectionFilter
from app.event_catalog import group_events, library_events
from app.rules import OCRLine


def line(text,x,y,w=.12,h=.025):
    return OCRLine(text,.95,[[x*1600,y*900],[(x+w)*1600,y*900],
                             [(x+w)*1600,(y+h)*900],[x*1600,(y+h)*900]])


def weapon(category='单手武器',name='陌生新武器'):
    return [line(category,.67,.15),line(name,.67,.20),
            line('伤害能力 247',.67,.27),line('破防能力 213',.86,.27)]


@pytest.mark.parametrize('navigation',[
    [line('地图',.26,.03),line('角色',.44,.03),line('道具',.61,.03)],
    [line('角色',.44,.03),line('道具',.61,.03)],
    [line('武器',.35,.03),line('影之武装',.46,.03)],
    [line('影之武装',.46,.03),line('回退',.90,.94)],
    [line('影之武装',.46,.03),line('按住消耗1080升级',.74,.85)],
    [line('装备',.76,.94),line('卸下',.85,.94)],
])
@pytest.mark.parametrize('category',['单手武器','远程','武学','破坏'])
def test_inventory_and_upgrade_weapon_titles_are_ignored(navigation,category):
    frame=np.zeros((900,1600,3),np.uint8)
    rows=navigation+weapon(category)
    assert not [e for e in detect_collection_candidates(rows,frame.shape,1,frame) if e['kind']=='weapon']
    engine=Mock()
    assert refine_collection_lines(rows,frame,engine)==rows
    engine.assert_not_called()


@pytest.mark.parametrize('category',['单手武器','远程','武学','破坏'])
def test_same_weapon_title_in_world_pickup_remains_first_collection(category):
    frame=np.zeros((900,1600,3),np.uint8); first=FirstCollectionFilter()
    rows=weapon(category)
    menu=[line('武器',.35,.03),line('影之武装',.46,.03)]+rows
    assert first.filter(detect_collection_candidates(menu,frame.shape,1,frame))==[]
    result=first.filter(detect_collection_candidates(rows,frame.shape,10,frame))
    assert [(r['kind'],r['name']) for r in result]==[('weapon','陌生新武器')]


def event(key,stamp,kind,name,**extra):
    return dict(id=key,video_id='v',kind=kind,name=name,time=stamp,end_time=stamp+1,
                source='ocr',evidence='collection_panel',text=name,verified=0,edited=0,
                screenshot=key+'.jpg',**extra)


@pytest.mark.parametrize('focused',[False,True])
def test_audited_menu_records_do_not_hide_later_world_pickup(focused):
    menu={**event('menu',1,'weapon','巨凶斧'),'evidence':'weapon_menu'}
    pickup=event('pickup',10,'weapon','巨凶斧')
    result=group_events([menu,pickup],collection_only=focused)
    assert [r['id'] for r in result]==['pickup']
    assert menu['evidence']=='weapon_menu'


def test_frame_verified_latest_aliases_merge_without_losing_chapters_or_fragments():
    rows=[event('mask1',1,'accessory','百相雄面'),event('mask2',2,'accessory','百相雒面'),
          event('task1',3,'task_rumor','勿相忘'),event('task2',4,'task_rumor','勿相'),
          event('bell1',5,'bell','維面祭台'),event('bell2',6,'bell','雒面祭台'),
          event('fragment1',10,'amber','杀气琥珀碎片'),event('whole',12,'amber','杀气琥珀'),
          event('fragment2',20,'amber','杀气琥珀碎片'),
          event('page1',30,'book','书籍 · 第一章'),event('page2',31,'book','书籍 · 第一章'),
          event('chapter2',32,'book','书籍 · 第二章'),event('reopen',40,'book','书籍 · 第一章')]
    result=library_events(group_events(rows,collection_only=True),[{'id':'v','import_order':1}])
    assert [r['id'] for r in result]==['mask1','task1','bell1','fragment1','whole','fragment2','page1','chapter2','reopen']
    assert result[0]['name']=='百相傩面' and result[2]['name']=='傩面祭台'
