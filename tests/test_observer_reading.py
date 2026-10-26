"""Synthetic regression cases; no real usernames or recording frames."""
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ada_poker import cards
from ada_poker.reading import (
    OBSERVER_LAYOUT, OBSERVER_NAMES, OBSERVER_STACKS, OBSERVER_ACTIONS, read_frame,
)


def observer_frame(width=952, height=710):
    words=[]
    def add(text, box):
        x,y,right,bottom=box
        words.append(dict(text=text,x=x*width/1418,y=y*height/1024,
                          width=(right-x)*width/1418,height=(bottom-y)*height/1024))
    add('Join Waiting',(1120,954,1360,986))
    for seat,(name,stack,action) in enumerate(zip(OBSERVER_NAMES,OBSERVER_STACKS,OBSERVER_ACTIONS)):
        add(f'Example{seat}',name)
        add('100 BB',stack)
        add('31 Check',action)
    return dict(width=width,height=height,words=words,felt_points=5,dealer_scores=[80]+[0]*6)


class ObserverReadingTests(unittest.TestCase):
    def test_scaled_names_do_not_include_action_badges(self):
        for width,height in ((952,710),(1418,1024),(1904,1420)):
            with self.subTest(size=(width,height)):
                o=read_frame(observer_frame(width,height),0,{})
                self.assertEqual(o['layout'],OBSERVER_LAYOUT)
                self.assertTrue(o['table_visible'])
                self.assertEqual([s['username'] for s in o['seats']],[f'Example{i}' for i in range(7)])
                self.assertTrue(all(s['stack_bb']==100 and s['action']=='check' for s in o['seats']))

    def test_waiting_text_elsewhere_does_not_select_profile(self):
        raw=observer_frame()
        raw['words'][0]['y']=20
        self.assertNotEqual(read_frame(raw,0,{})['layout'],OBSERVER_LAYOUT)

    def test_observer_never_uses_hero_or_show_controls(self):
        with patch.object(cards,'hero_glyphs',side_effect=AssertionError('Not a hero')), \
             patch.object(cards,'show_button_glyphs',side_effect=AssertionError('No controls')):
            pixels=bytes([20,100,40])*(cards.WIDTH*cards.HEIGHT)
            result=cards.read_cards(pixels,cards.load_templates(OBSERVER_LAYOUT),OBSERVER_LAYOUT)
        self.assertEqual(result['board'],[])
        self.assertIsNone(result['hero_cards'])
        self.assertEqual(result['hero_candidates'],[])
        self.assertTrue(all(s['cards'] is None for s in result['exposed_cards']))

    def test_observer_templates_are_separate_from_legacy(self):
        legacy=cards.load_templates()
        observer=cards.load_templates(OBSERVER_LAYOUT)
        self.assertNotEqual(legacy['profile'],observer['profile'])
        self.assertTrue({'4','6'} <= {t['label'] for t in observer['ranks']})

    def test_partial_observer_bottom_seat_is_not_overwritten_by_hero(self):
        observations=[dict(time=i/5,table_visible=True,layout=OBSERVER_LAYOUT,
                           exposed_cards=[dict(seat=4,candidates=[dict(rank='A',suit=None),dict(rank='K',suit=None)])])
                      for i in range(2)]
        cards.add_visible_hole_cards(observations)
        self.assertEqual(observations[0]['visible_hole_cards'][0]['seat'],4)
        self.assertEqual(observations[0]['visible_hole_cards'][0]['cards'][0]['rank'],'A')

    def test_card_cache_cannot_reuse_different_layout(self):
        def observation(layout):
            return [dict(time=0,table_visible=True,layout=layout,issues=[],
                         evidence=dict(image='synthetic.jpg'))]
        def reading(*args):
            return dict(board=[],hero_cards=None,hero_candidates=[],exposed_cards=[],card_issues=[])
        with TemporaryDirectory() as output, \
             patch.object(cards,'image_sequence',side_effect=lambda *args: iter([b''])), \
             patch.object(cards,'read_cards',side_effect=reading) as reader:
            cards.add_card_readings(observation(None),output,'unused','source',lambda _:None)
            cards.add_card_readings(observation(None),output,'unused','source',lambda _:None)
            self.assertEqual(reader.call_count,1)
            cards.add_card_readings(observation(OBSERVER_LAYOUT),output,'unused','source',lambda _:None)
            self.assertEqual(reader.call_count,2)
