from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
import hashlib
import json
import unittest

from scripts.check_hand_reference import compare
from ada_poker.storage import save_analysis
from ada_poker.ui_data import Library
from ui_fixtures import hand


class ReferenceTests(unittest.TestCase):
    def test_wrong_street_missing_cards_and_extra_hands_fail(self):
        actual={'start_time':0,'end_time':10,'board':None,'seats':[],
                'events':[{'time':5,'seat':0,'action':'bet','street':'preflop','to_bb':3.2}]}
        expected={'index':0,'start_time':0,'end_time':10,'board':['As','Js','Jh'],
                  'required_events':[{'time':5,'seat':0,'action':'bet','street':'flop','bb':3.2}]}
        report=compare([actual,deepcopy(actual)],{'hands':[expected]})
        self.assertFalse(report['hand_count_matches'])
        self.assertEqual(len(report['results'][0]['failures']),2)
        self.assertFalse(report['completeness_verified'])

    def test_test_session_excluded_without_database_mutation_and_refreshes(self):
        with TemporaryDirectory(dir='.dev') as folder:
            root=Path(folder);db=root/'players.sqlite3';h=hand()
            save_analysis(db,'example','source',[h],{})
            original=hashlib.sha256(db.read_bytes()).hexdigest()
            library=Library(root)
            self.assertTrue(library.search('Mira'))
            settings=root/'session-purposes.json'
            settings.write_text(json.dumps({'example':'test'}))
            self.assertEqual(library.search('Mira'),[])
            self.assertEqual(library.hands,{})
            settings.write_text('{}')
            self.assertTrue(library.search('Mira'))
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(),original)
