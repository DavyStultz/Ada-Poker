import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import unittest
import uuid

from ada_poker.storage import save_analysis, search_players, load_player_hands
from ada_poker.player_view import hand_text, evidence_url


def hand(session, index=0, name='Opponent', hero=True):
    seats = [{'seat': 0, 'username': name, 'participant': None}]
    if hero:
        seats.append({'seat': 4, 'username': 'MyName', 'participant': True})
    return {'id': f'{session}:{index}', 'session_id': session, 'start_time': index,
            'end_time': index+1, 'seats': seats, 'needs_review': True, 'issues': [],
            'events': [{'action': 'fold', 'username': name, 'time': index, 'street': 'preflop'}]}


class PlayerLookupTests(unittest.TestCase):
    def setUp(self):
        self.folder = (Path('.dev/unit-tests') / uuid.uuid4().hex).resolve()
        self.folder.mkdir(parents=True)
        self.db = self.folder / 'players.sqlite3'

    def tearDown(self):
        assert self.folder.is_relative_to((Path.cwd()/'.dev/unit-tests').resolve())
        shutil.rmtree(self.folder)

    def save(self, session, hands):
        save_analysis(self.db,session,'source/'+session,hands,{})

    def test_shared_table_includes_folds_and_multiple_sessions(self):
        self.save('one',[hand('one')])
        self.save('two',[hand('two')])
        self.assertEqual(search_players(self.db,'opp'),[
            {'username':'Opponent','hand_count':2,'session_count':2}])
        records = load_player_hands(self.db,'Opponent')
        self.assertEqual([r['session_id'] for r in records],['two','one'])
        self.assertEqual(records[0]['hand']['events'][0]['action'],'fold')
        self.assertTrue(records[0]['hand']['needs_review'])

    def test_hero_excluded_and_missing_hero_not_claimed_shared(self):
        self.save('one',[hand('one'),hand('one',1,'OnlyThem',hero=False)])
        self.assertEqual([r['username'] for r in search_players(self.db)],['Opponent'])
        self.assertEqual(load_player_hands(self.db,'MyName'),[])
        self.assertEqual(load_player_hands(self.db,'OnlyThem'),[])

    def test_spelling_and_case_variants_are_separate_identities(self):
        names = ['Player','player','PIayer']
        self.save('one',[hand('one',i,n) for i,n in enumerate(names)])
        self.assertEqual(len(search_players(self.db,'player')),2)
        for name in names:
            self.assertEqual(len(load_player_hands(self.db,name)),1)
        self.assertEqual(load_player_hands(self.db,'PLAYER'),[])

    def test_query_is_literal_and_parameterized(self):
        names = ['a_b','axb','a%b',r'a\b',"O'Brien"]
        self.save('one',[hand('one',i,n) for i,n in enumerate(names)])
        for query in ['_','%', '\\', "'"]:
            self.assertEqual(len(search_players(self.db,query)),1)
        self.assertEqual(search_players(self.db,"' OR 1=1 --"),[])
        self.assertEqual(load_player_hands(self.db,"' OR 1=1 --"),[])

    def test_duplicate_sightings_do_not_duplicate_hands(self):
        h = hand('one')
        h['seats'].append({'seat':2,'username':'Opponent'})
        self.save('one',[h])
        self.assertEqual(search_players(self.db)[0]['hand_count'],1)
        self.assertEqual(len(load_player_hands(self.db,'Opponent')),1)

    def test_reprocessing_correction_moves_lookup_preserves_other_session(self):
        self.save('one',[hand('one',name='Typo')])
        self.save('two',[hand('two',name='Typo')])
        self.save('one',[hand('one',name='Corrected')])
        self.save('one',[hand('one',name='Corrected')])
        self.assertEqual([r['session_id'] for r in load_player_hands(self.db,'Typo')],['two'])
        self.assertEqual(len(load_player_hands(self.db,'Corrected')),1)

    def test_hand_and_player_pagination_stable_without_overlap(self):
        self.save('one',[hand('one',i) for i in range(55)] + [hand('one',100+i,f'Other{i:02}') for i in range(55)])
        a = load_player_hands(self.db,'Opponent',limit=50)
        b = load_player_hands(self.db,'Opponent',limit=50,offset=50)
        self.assertEqual(len(a),50)
        self.assertEqual(len(b),5)
        self.assertEqual(len({r['hand']['id'] for r in a+b}),55)
        p = search_players(self.db,limit=50)+search_players(self.db,limit=50,offset=50)
        self.assertEqual(len({r['username'] for r in p}),56)

    def test_missing_db_is_empty_without_creating_it(self):
        self.assertEqual(search_players(self.db),[])
        self.assertEqual(load_player_hands(self.db,'any'),[])
        self.assertFalse(self.db.exists())

    def test_invalid_pagination_rejected(self):
        for limit, offset in [(0,0),(101,0),(True,0),(1,-1),(1,False)]:
            with self.assertRaises(ValueError):
                search_players(self.db,limit=limit,offset=offset)

    def test_v1_readable_and_migration_preserves_hands_and_corrections(self):
        self.save('one',[hand('one')])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP INDEX idx_sightings_username')
            conn.execute('DROP INDEX idx_sightings_hand_seat')
            conn.execute('PRAGMA user_version=1')
            conn.execute('INSERT INTO corrections VALUES (?,?,?,?)',('one:0','one',json.dumps({'needs_review':False}),'now'))
        self.assertFalse(load_player_hands(self.db,'Opponent')[0]['hand']['needs_review'])
        self.save('two',[hand('two')])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertEqual(search_players(self.db)[0]['hand_count'],2)
        self.assertFalse(load_player_hands(self.db,'Opponent')[1]['hand']['needs_review'])

    def test_future_database_rejected_for_lookup(self):
        self.save('one',[hand('one')])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):
            search_players(self.db)

    def test_evidence_link_identifies_hand_and_rejects_traversal(self):
        self.save('one',[hand('one')])
        record = load_player_hands(self.db,'Opponent')[0]
        with self.assertRaises(FileNotFoundError):
            evidence_url(self.folder,record)
        (self.folder/'one').mkdir()
        (self.folder/'one/review.html').write_text('test',encoding='utf-8')
        self.assertTrue(evidence_url(self.folder,record).endswith('#hand=one%3A0'))
        record['session_id'] = '../escape'
        with self.assertRaises(ValueError):
            evidence_url(self.folder,record)

    def test_readable_details_preserve_uncertainty_and_total_semantics(self):
        h = hand('one')
        h['events'].append({'time':2,'action':'call','username':'Opponent','to_bb':3,'amount_bb':None})
        rendered = hand_text(h)
        self.assertIn('Needs checking',rendered)
        self.assertIn('displayed total 3 BB (increment unknown)',rendered)
        self.assertIn('Opponent: fold',rendered)
        self.assertIn('participation may be unknown',rendered)
