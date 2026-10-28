import unittest

from app.rules import OCRLine
from app.text_index import TextIndexFilter


SHAPE=(1000,1600,3)


def candidate(name,kind='text',confidence=.8,**extra):
    return {'kind':kind,'name':name,'text':name,'confidence':confidence,**extra}


def line(text,left=.40,top=.08,right=.60,bottom=.12,score=.8):
    return OCRLine(text,score,[[left*1600,top*1000],[right*1600,top*1000],
                               [right*1600,bottom*1000],[left*1600,bottom*1000]])


class TextIndexTests(unittest.TestCase):
    def test_dialogue_speaker_prefix_dropout_keeps_complete_name(self):
        f=TextIndexFilter()
        first=f.filter([candidate('登高眺望的行者',confidence=.8,evidence='dialogue_speaker')],[line('登高眺望的行者',.42,.8,.58,.83)],SHAPE,0)[0]
        short=f.filter([candidate('眺望的行者',confidence=.9,evidence='dialogue_speaker')],[line('眺望的行者',.45,.8,.55,.83)],SHAPE,1)[0]
        assert short['_identity_key']==first['_identity_key']
        assert short['name']=='登高眺望的行者'

    def test_specialized_scene_keeps_main_events_without_secondary_text(self):
        for kind in ('item','waypoint','quest'):
            main=candidate('主要内容',kind)
            clutter=[candidate('其他地点'),candidate('目录标题')]
            self.assertEqual(TextIndexFilter().filter([main,*clutter],[],SHAPE,0),[main])

    def test_nontext_true_repeat_events_are_never_filtered_or_merged(self):
        events=[candidate('任务目标很长,而且故意带正文以及多种标点',kind=kind)
                for kind in ('item','enemy','achievement','quest','waypoint')]
        events+=events[:1]
        f=TextIndexFilter()
        out=f.filter(events,[],SHAPE,0)
        self.assertEqual(out,events)
        self.assertTrue(all(a is b for a,b in zip(out,events)))
        self.assertTrue(all('_merge_gap' not in e for e in out))

    def test_all_passes_every_candidate_and_off_only_removes_text(self):
        events=[candidate('R2'),candidate('获得物品',kind='item')]
        self.assertEqual(TextIndexFilter('all').filter(events),events)
        self.assertEqual(TextIndexFilter('off').filter(events),events[1:])

    def test_controls_stats_and_prose_are_removed_but_resource_names_remain(self):
        removed=['R2','RC4','BOSS','1 返回','α 确定','口查看收集详情','伤害能力 272>299',
                 '意识尘埃 + 390','我也是无意中,食用了','这种杀气改造塔是昔年',
                 '按住β消耗+1620升级','消耗+3240升级']
        f=TextIndexFilter()
        out=f.filter([candidate(x) for x in removed+['浑源石','铸造金属+16','溶剂 +8']],[],SHAPE,0)
        self.assertEqual([x['name'] for x in out],['浑源石','铸造金属+16','溶剂 +8'])

    def test_bottom_location_and_task_hint_are_not_blanket_deleted(self):
        names=['远眺龙镇之桥','在钢铁巨塔的顶层寻找丢失的钥匙','连续不断的这些文字是角色正在说的话']
        lines=[line(names[0],.03,.84,.20,.89),line(names[1],.62,.81,.94,.86),line(names[2],.25,.89,.75,.94)]
        out=TextIndexFilter().filter([candidate(x) for x in names],lines,SHAPE,0)
        self.assertEqual([x['name'] for x in out],names[:2])

    def test_book_panel_body_is_removed_even_when_wrapped_into_short_lines(self):
        names=['书籍','四凶斧案卷','四凶斧之乱·混乱','人阁建造的']
        lines=[line(names[0],.67,.17,.74,.19),line(names[1],.67,.21,.83,.24),
               line(names[2],.67,.27,.89,.30),line(names[3],.67,.46,.88,.50)]
        out=TextIndexFilter().filter([candidate(x) for x in names],lines,SHAPE,0)
        self.assertEqual([x['name'] for x in out],names[1:3])

    def test_specialized_name_and_evidence_do_not_get_a_duplicate_text_entry(self):
        item=candidate('痴者残剑','item',evidence='item_panel')
        item['text']='武器展示面板\n单手武器\n痴者残剑\n装备新的武器？'
        out=TextIndexFilter().filter([item,candidate('痴者残剑'),candidate('装备新的武器？')],[],SHAPE,0)
        self.assertEqual(out,[item])

    def test_nearby_boss_typo_uses_stable_identity_and_best_reading(self):
        f=TextIndexFilter()
        first=f.filter([candidate('剑痴:花干笑',confidence=.67)],[line('剑痴:花干笑')],SHAPE,0)[0]
        better=f.filter([candidate('剑痴:花千笑',confidence=.94)],[line('剑痴:花千笑')],SHAPE,1)[0]
        repeated=f.filter([candidate('剑痴:花干笑',confidence=.70)],[line('剑痴:花干笑')],SHAPE,10)[0]
        self.assertEqual(first['_identity_key'],better['_identity_key'])
        self.assertEqual(better['name'],'剑痴:花千笑')
        self.assertEqual(repeated['name'],better['name'])
        self.assertEqual(repeated['_identity_key'],first['_identity_key'])
        self.assertEqual(better['_merge_gap'],20)
        self.assertTrue(better['_replace_name'])

    def test_two_chapters_and_numbered_locations_are_distinct(self):
        for a,b in [('四凶斧之乱·混乱','四凶斧之乱·祸乱'),('溪谷杀气塔一层','溪谷杀气塔二层')]:
            f=TextIndexFilter()
            one=f.filter([candidate(a)],[line(a)],SHAPE,0)[0]
            two=f.filter([candidate(b)],[line(b)],SHAPE,1)[0]
            self.assertNotEqual(one['_identity_key'],two['_identity_key'])

    def test_different_spatial_labels_do_not_fuzzy_merge(self):
        f=TextIndexFilter()
        one=f.filter([candidate('剑痴:花干笑')],[line('剑痴:花干笑')],SHAPE,0)[0]
        two=f.filter([candidate('剑痴:花千笑')],[line('剑痴:花千笑',.40,.60,.60,.64)],SHAPE,1)[0]
        self.assertNotEqual(one['_identity_key'],two['_identity_key'])

    def test_minute_later_is_a_new_track_not_old_best_spelling(self):
        f=TextIndexFilter()
        f.filter([candidate('剑痴:花干笑',confidence=.6)],[line('剑痴:花干笑')],SHAPE,0)
        f.filter([candidate('剑痴:花千笑',confidence=.95)],[line('剑痴:花千笑')],SHAPE,1)
        later=f.filter([candidate('剑痴:花干笑',confidence=.7)],[line('剑痴:花干笑')],SHAPE,61)[0]
        self.assertEqual(later['name'],'剑痴:花干笑')
        self.assertEqual(f.state_size,1)

    def test_full_title_consumes_duplicate_name_component(self):
        out=TextIndexFilter().filter([candidate('剑痴:花千笑'),candidate('花千笑')],[],SHAPE,0)
        self.assertEqual([e['name'] for e in out],['剑痴:花千笑'])

    def test_memory_and_diagnostics_are_bounded_for_hours_of_text(self):
        f=TextIndexFilter(max_tracks=16)
        for i in range(200):
            f.filter([candidate('测试地点'+str(i))],[],SHAPE,i/100)
            self.assertLessEqual(f.state_size,16)
            self.assertLessEqual(len(f.last_decisions),100)
        f.filter([],[],SHAPE,100)
        self.assertEqual(f.state_size,0)

    def test_backwards_seek_resets_local_history(self):
        f=TextIndexFilter()
        f.filter([candidate('剑痴:花千笑',confidence=.95)],[line('剑痴:花千笑')],SHAPE,100)
        result=f.filter([candidate('剑痴:花干笑',confidence=.6)],[line('剑痴:花干笑')],SHAPE,10)[0]
        self.assertEqual(result['name'],'剑痴:花干笑')


if __name__=='__main__':unittest.main()
