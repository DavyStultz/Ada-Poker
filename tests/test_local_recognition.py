from copy import deepcopy
import unittest
from unittest.mock import patch
from pathlib import Path
import os
import stat
import uuid

from ada_poker.names import apply_name_evidence, panel_names, selected_name_frames, temporary_panels
from ada_poker.reading import stabilize_names
from ada_poker.cards import add_visible_hole_cards, read_cards
from ada_poker.history import reconstruct
from ada_poker.corrections import apply_name_corrections


def observation(time, name='Player', visible=True):
    return {'time':time,'table_visible':visible,'issues':[], 'dealer_seat':0,
            'evidence':{'image':f'{time}.jpg'},
            'seats':[{'seat':0,'username':name,'raw_username':name,'stack_bb':100}] if visible else []}


class LocalNameTests(unittest.TestCase):
    def test_readonly_temporary_folder_is_cleaned(self):
        root=(Path('.dev/unit-tests')/uuid.uuid4().hex).resolve()
        root.mkdir(parents=True)
        try:
            with temporary_panels(root) as folder:
                (folder/'sample.bmp').write_bytes(b'generated test')
                os.chmod(folder,stat.S_IREAD)
            self.assertFalse(folder.exists())
        finally:
            assert root.parent==(Path.cwd()/'.dev/unit-tests').resolve()
            os.chmod(root,stat.S_IWRITE)
            root.rmdir()

    def test_repeat_support_does_not_cross_time_gap(self):
        result=stabilize_names([observation(0),observation(5)])
        self.assertTrue(all(o['seats'][0]['username'] is None for o in result))

    def test_repeat_support_does_not_cross_missing_table(self):
        result=stabilize_names([observation(0),observation(.2,visible=False),observation(.4)])
        self.assertIsNone(result[0]['seats'][0]['username'])
        self.assertIsNone(result[2]['seats'][0]['username'])

    def test_repeated_exact_spellings_remain_separate(self):
        result=stabilize_names([observation(0,'Player'),observation(.2,'Player'),
                                observation(.4,'PIayer'),observation(.6,'PIayer')])
        self.assertEqual([o['seats'][0]['username'] for o in result],['Player','Player','PIayer','PIayer'])
        self.assertEqual(result[0]['seats'][0]['username_status'],'conflicting_readings')

    def test_repeated_crops_recover_missing_text_without_rewriting_raw(self):
        obs=[observation(0,None),observation(.2,None)]
        result=apply_name_evidence(obs,{0:['Seen']+[None]*6,1:['Seen']+[None]*6})
        self.assertEqual(result[0]['seats'][0]['username'],'Seen')
        self.assertIsNone(result[0]['seats'][0]['raw_username'])
        self.assertEqual(result[0]['seats'][0]['username_source'],'ocr_crop')

    def test_single_crop_or_empty_seat_cannot_create_identity(self):
        obs=[observation(0,None),observation(.2,None)]
        obs[1]['seats'][0]['stack_bb']=None
        result=apply_name_evidence(obs,{0:['Seen']+[None]*6})
        self.assertIsNone(result[0]['seats'][0]['username'])
        result=apply_name_evidence(obs,{0:['Seen']+[None]*6,1:['Seen']+[None]*6})
        self.assertIsNone(result[1]['seats'][0]['username'])

    def test_disagreeing_crop_does_not_silently_rename_player(self):
        result=apply_name_evidence([observation(0,'Original')],{0:['Different']+[None]*6})
        self.assertEqual(result[0]['seats'][0]['username'],'Original')
        self.assertEqual(result[0]['seats'][0]['username_status'],'conflicting_readings')

    def test_crop_only_name_can_be_manually_corrected(self):
        obs=observation(0,None)
        obs['seats'][0]['crop_username']='Al1ce'
        result=apply_name_corrections([obs],{(0,'Al1ce'):'Alice'})
        self.assertEqual(result[0]['seats'][0]['username'],'Alice')
        self.assertIsNone(result[0]['seats'][0]['raw_username'])

    def test_refresh_reads_twice_but_not_every_frame(self):
        obs=[observation(i/5) for i in range(55)]
        self.assertEqual(selected_name_frames(obs),[0,1,26,27,52,53])

    def test_panel_keeps_username_spaces_and_discards_border_noise(self):
        def word(text,x,y,h=40):return {'text':text,'x':x,'y':y,'width':20,'height':h}
        names=panel_names({'words':[word('[',1,1,15),word('Two',10,30),word('Words',120,30)]})
        self.assertEqual(names[0],'Two Words')
        self.assertIsNone(names[1])


class VisibleCardTests(unittest.TestCase):
    def test_suit_fragment_without_rank_is_not_exposed_card_evidence(self):
        frames=self.frames()
        for o in frames:
            for card in o['exposed_cards'][0]['candidates']:
                card['rank']=None
        add_visible_hole_cards(frames)
        self.assertTrue(all(o['visible_hole_cards']==[] for o in frames))

    def frames(self):
        frames=[observation(0),observation(.2)]
        for o in frames:
            o['exposed_cards']=[{'seat':0,'cards':None,'candidates':[
                {'rank':'A','suit':None,'card':None},{'rank':'K','suit':'h','card':'Kh'}]}]
        return frames

    def test_repeated_partial_cards_survive_without_inventing_suit(self):
        frames=self.frames()
        add_visible_hole_cards(frames)
        hand=reconstruct('test',frames)[0]
        self.assertIsNone(hand['seats'][0]['hole_cards'])
        self.assertEqual(hand['seats'][0]['hole_card_parts'],[{'rank':'A','suit':None},{'rank':'K','suit':'h'}])
        self.assertTrue(hand['seats'][0]['hole_card_evidence'])

    def test_one_frame_match_and_time_gap_do_not_confirm_cards(self):
        frames=self.frames()
        add_visible_hole_cards(frames[:1])
        self.assertEqual(frames[0]['visible_hole_cards'],[])
        frames[1]['time']=2
        add_visible_hole_cards(frames)
        self.assertEqual(frames[0]['visible_hole_cards'],[])

    def test_card_conflict_is_retained_in_hand(self):
        frames=self.frames()
        add_visible_hole_cards(frames)
        later=deepcopy(frames[1]);later['time']=.4
        later['visible_hole_cards'][0]['cards'][0]['rank']='Q'
        seat=reconstruct('test',frames+[later])[0]['seats'][0]
        self.assertIsNone(seat['hole_card_parts'][0]['rank'])
        self.assertIn('hole_card_readings_conflict',seat['issues'])

    def test_duplicate_complete_cards_across_seats_are_rejected(self):
        pairs=[(['As','Kd'],[]),(None,[]),(['As','Tc'],[])]+[(None,[])]*5
        with patch('ada_poker.cards.read_board',return_value={'board':[]}), patch('ada_poker.cards.pair_reading',side_effect=pairs):
            result=read_cards(b'',{})
        self.assertIsNone(result['hero_cards'])
        self.assertIsNone(result['exposed_cards'][0]['cards'])
        self.assertIn('duplicate_cards_across_seats',result['card_issues'])
