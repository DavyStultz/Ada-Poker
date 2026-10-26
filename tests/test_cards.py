import unittest
from ada_poker.cards import WIDTH,HEIGHT,glyph,match_glyph,load_templates,read_cards


class CardTests(unittest.TestCase):
    def test_missing_glyph_remains_unknown(self):
        self.assertEqual(match_glyph(None,load_templates()['ranks']),(None,0.0))

    def test_identical_labels_do_not_compete_with_themselves(self):
        template=load_templates()['ranks'][0]
        self.assertEqual(match_glyph(template,[template,template])[0],template['label'])

    def test_equal_scores_for_different_labels_are_rejected(self):
        template=load_templates()['ranks'][0]
        rival={**template,'label':'Q'}
        self.assertIsNone(match_glyph(template,[template,rival])[0])

    def test_wrong_aspect_is_rejected(self):
        template=load_templates()['ranks'][0]
        self.assertIsNone(match_glyph({**template,'aspect':template['aspect']*3},[template])[0])

    def test_blank_felt_does_not_create_cards(self):
        pixels=bytes([20,100,40])*(WIDTH*HEIGHT)
        result=read_cards(pixels,load_templates())
        self.assertEqual(result['board'],[])
        self.assertIsNone(result['hero_cards'])

    def test_empty_bright_crop_is_not_a_glyph(self):
        pixels=bytes([220,220,220])*(WIDTH*HEIGHT)
        self.assertIsNone(glyph(pixels,(10,10,50,50),220))

