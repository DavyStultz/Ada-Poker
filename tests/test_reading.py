import unittest
from ada_poker.reading import bb_amount, read_frame, NAME_BOXES, STACK_BOXES


def word(text,x,y,width=50,height=16):
    return dict(text=text,x=x,y=y,width=width,height=height)


def sample(felt=5):
    words=[]
    for i in (0,1,4):
        x,y,_,_=NAME_BOXES[i]
        words.append(word(f'Player{i}',x+4,y+4))
        x,y,_,_=STACK_BOXES[i]
        words.append(word('100 BB',x+4,y+4))
    return dict(width=1418,height=1024,words=words,felt_points=felt,dealer_scores=[0,0,0,0,80,0,0])


class ReadingTests(unittest.TestCase):
    def test_upper_left_commitment_region(self):
        raw=sample()
        raw['words'].append(word('2.5BB',499,324))
        self.assertEqual(read_frame(raw,14.2,{})['seats'][0]['bet_bb'],2.5)
    def test_explicit_bb_and_common_suffix_errors(self):
        for text,value in [('0 BB',0),('1BB',1),('IBB',1),('2.58B',2.5),('+180.3 BB',180.3)]:
            self.assertEqual(bb_amount(text),value)

    def test_invalid_money_is_unknown(self):
        for text in ('-5 BB','NaN BB','inf BB','100','foo100BB','1e3BB','1.2.3BB',''):
            self.assertIsNone(bb_amount(text),text)

    def test_board_covering_felt_is_still_a_table(self):
        result=read_frame(sample(felt=2),0,{})
        self.assertTrue(result['table_visible'])
        self.assertEqual(result['dealer_seat'],4)

    def test_wallpaper_gold_is_not_a_dealer(self):
        raw=sample(felt=0)
        raw['words']=[]
        result=read_frame(raw,80,{})
        self.assertFalse(result['table_visible'])
        self.assertIsNone(result['dealer_seat'])
        self.assertEqual(result['seats'],[])

    def test_action_label_ignores_level_badge(self):
        raw=sample()
        raw['words'] += [word('31',451,170,15),word('Fold',497,166)]
        result=read_frame(raw,40,{})
        self.assertEqual(result['seats'][0]['action'],'fold')
        self.assertIsNone(result['hero_cards'])

    def test_card_unknown_is_not_an_empty_board(self):
        self.assertIsNone(read_frame(sample(),0,{})['board'])
