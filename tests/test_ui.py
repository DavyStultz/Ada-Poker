from contextlib import closing
from copy import deepcopy
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import threading
import unittest
import uuid

from ada_poker.storage import save_analysis
from ada_poker.ui import AppServer
from ada_poker.ui_data import Library, name_key, position_group, public_hand
from ui_fixtures import hand, record, examples


class LibraryTests(unittest.TestCase):
    def library(self, *hands):
        return Library('.dev/no-such-library', [record(h) for h in hands])

    def test_blank_search_and_shared_table_folds(self):
        h=hand(); h['events']=[{'seat':0,'street':'preflop','action':'fold'}]
        lib=self.library(h)
        self.assertEqual(lib.search(''),[])
        self.assertEqual(lib.search('Hero'),[])
        self.assertEqual(lib.search('Mira')[0]['count'],1)
        h['seats']=[s for s in h['seats'] if s['seat']!=4]
        self.assertEqual(self.library(h).search('Mira'),[])

    def test_narrow_alias_grouping_preserves_originals(self):
        a,b,c=hand(0),hand(1,'Mirall'),hand(2,'Mira17')
        before=deepcopy([a,b,c]);lib=self.library(a,b,c)
        profiles=lib.search('Mira')
        self.assertEqual(len(profiles),2)
        self.assertEqual(lib.search('Mirall')[0]['aliases'],['Mira11','Mirall'])
        self.assertEqual(lib.results(name_key('Mira11'))['total'],2)
        self.assertEqual([a,b,c],before)
        self.assertNotEqual(name_key('Player'),name_key('player'))

    def test_explicit_preflop_and_any_identity_uncertainty_hides_hand(self):
        a,b,c=hand(0),hand(1),hand(2)
        a['certainty']={'preflop':False}
        b['seats'][1]['username_certain']=False
        c['certainty']={'username':'unknown'}
        self.assertEqual(self.library(a,b,c).search('Mira'),[])

    def test_only_lookalike_uncertainty_is_permitted(self):
        h=hand(); h['seats'][0].update(username_certain=False,username_candidates=['Mirall','Mira11'])
        self.assertEqual(self.library(h).search('Mira')[0]['count'],1)
        h['seats'][0]['username_candidates'].append('Mira17')
        self.assertEqual(self.library(h).search('Mira'),[])

    def test_legacy_review_flag_does_not_change_supplied_ui_certainty(self):
        h=hand(); h['issues']=['Legacy draft']; lib=self.library(h)
        self.assertEqual(lib.results(name_key('Mira11'))['total'],1)
        self.assertTrue(h['needs_review'])

    def test_uncertain_postflop_retained_but_not_action_filtered(self):
        h=hand();h['certainty']={'turn':{'certain':False}}
        lib=self.library(h);key=name_key('Mira11')
        self.assertEqual(lib.results(key)['total'],1)
        self.assertFalse(lib.hand(h['id'],key)['streets']['turn']['certain'])
        self.assertEqual(lib.results(key,{'street':'turn','action':'check'})['total'],0)
        self.assertEqual(lib.results(key,{'action':'raise','size':'2'})['total'],1)

    def test_raise_filters_must_match_the_same_action(self):
        lib=self.library(hand());key=name_key('Mira11')
        for filters in ({'action':'raise','order':'1','size':'2','position':'early'},
                        {'action':'reraise','order':'3','size':'18'}):
            self.assertEqual(lib.results(key,filters)['total'],1)
        for filters in ({'order':'1','size':'18'},{'order':'2'},
                        {'action':'reraise','size':'2'},{'position':'late'},
                        {'action':'raise','size':'2.5'}):
            self.assertEqual(lib.results(key,filters)['total'],0)

    def test_raise_order_resets_each_street_including_all_in_raise(self):
        h=hand();h['events'].append({'seat':0,'street':'flop','action':'all_in','is_raise':True,'to_bb':75})
        events=public_hand(h,'s')['events']
        self.assertEqual([e['raise_order'] for e in events if e['raise_order']],[1,2,3,1,2])
        lib=self.library(h)
        self.assertEqual(lib.results(name_key('Mira11'),{'street':'flop','action':'reraise','order':'2','size':'75'})['total'],1)

    def test_call_size_matches_displayed_increment_when_both_amounts_exist(self):
        h=hand();h['events']=[{'seat':0,'street':'preflop','action':'call','to_bb':6,'amount_bb':4}]
        lib=self.library(h);key=name_key('Mira11')
        self.assertEqual(lib.results(key,{'action':'call','size':'4'})['total'],1)
        self.assertEqual(lib.results(key,{'action':'call','size':'6'})['total'],0)

    def test_broad_positions_follow_occupied_clockwise_order(self):
        h=hand()
        self.assertEqual([position_group(h,s) for s in h['seats']],['early','early','late','late','late','blinds','blinds'])
        # Dealer 0, sparse blinds 3/4: first nonblind is 5, not the dealer.
        h={'dealer_seat':0,'seats':[{'seat':0,'position':'BTN'}, {'seat':3,'position':'SB'},
                                  {'seat':4,'position':'BB'},{'seat':5,'position':'UTG'},{'seat':6,'position':'CO'}]}
        self.assertEqual([position_group(h,s) for s in h['seats']],['late','blinds','blinds','early','early'])
        h['seats']=[{'seat':0,'position':'SB'},{'seat':4,'position':'BB'}]
        self.assertEqual([position_group(h,s) for s in h['seats']],['blinds','blinds'])

    def test_cards_priority_partial_cards_and_pagination(self):
        lib=Library('.dev/no-such-library',examples());key=name_key('Mira11')
        result=lib.results(key)
        self.assertEqual((result['total'],result['pages'],len(result['hands'])),(34,2,30))
        self.assertEqual([h['id'] for h in result['hands'][:2]],['example:0','example:1'])
        self.assertEqual(len(lib.results(key,page=999)['hands']),4)
        self.assertEqual(lib.results(key,{'cards':'seen'})['total'],2)
        self.assertEqual(lib.results(key,{'cards':'showdown'})['total'],1)
        self.assertEqual(lib.results(key,{'cards':'unseen'})['total'],32)
        h=hand();h['seats'][0]['hole_card_parts']=[{'rank':'A'},{'rank':'A'}]
        result=self.library(h).results(key,{'cards':'seen'})
        self.assertEqual(result['hands'][0]['cards'],['A?','A?'])

    def test_unknown_position_cannot_match_exact_position_filter(self):
        h=hand();h['seats'][0]['position']=None
        lib=self.library(h);key=name_key('Mira11')
        self.assertEqual(lib.results(key)['total'],1)
        self.assertEqual(lib.results(key,{'position':'early'})['total'],0)

    def test_missing_other_position_does_not_shift_early_group(self):
        h=hand();h['seats'][5]['position']=None
        self.assertIsNone(position_group(h,h['seats'][0]))
        self.assertEqual(position_group(h,h['seats'][6]),'blinds')

    def test_showdown_filter_requires_visible_cards(self):
        h=hand();h['seats'][0]['showdown']=True
        self.assertEqual(self.library(h).results(name_key('Mira11'),{'cards':'showdown'})['total'],0)

    def test_missing_amount_cannot_match_zero_size(self):
        h=hand();h['events']=[{'seat':0,'street':'preflop','action':'raise'}]
        lib=self.library(h)
        self.assertEqual(lib.results(name_key('Mira11'),{'action':'raise','size':'0'})['total'],0)

    def test_explicit_order_decimal_size_and_street_filters(self):
        h=hand();h['events']=[{'seat':0,'street':'turn','action':'raise','raise_order':3,'raise_to_bb':2.5,'to_bb':9}]
        lib=self.library(h);key=name_key('Mira11')
        self.assertEqual(lib.results(key,{'street':'turn','order':'3','size':'2.5','action':'reraise'})['total'],1)
        self.assertEqual(lib.results(key,{'street':'flop','order':'3','size':'2.5'})['total'],0)

    def test_search_literal_special_characters(self):
        lib=self.library(hand(0,'A%_B'),hand(1,'AXXB'),hand(2,'<b>Example</b>'))
        self.assertEqual([p['name'] for p in lib.search('%_')],['A%_B'])
        self.assertEqual(lib.search('<b>')[0]['name'],'<b>Example</b>')


class FileTests(unittest.TestCase):
    def setUp(self):
        self.root=(Path('.dev/unit-tests')/uuid.uuid4().hex).resolve()
        self.root.mkdir(parents=True)

    def tearDown(self):
        assert self.root.parent==(Path.cwd()/'.dev/unit-tests').resolve()
        def writable(fn,path,exc):
            Path(path).chmod(stat.S_IWRITE);fn(path)
        shutil.rmtree(self.root,onexc=writable)

    def test_database_readonly_corrections_and_refresh(self):
        db=self.root/'players.sqlite3';h=hand()
        save_analysis(db,'s','source',[h],{})
        with closing(sqlite3.connect(db)) as conn,conn:
            conn.execute('INSERT INTO corrections VALUES (?,?,?,?)',(h['id'],'s',json.dumps({'pot_bb':99}),'now'))
        before=hashlib.sha256(db.read_bytes()).digest()
        lib=Library(self.root);key=lib.search('Mira')[0]['key']
        self.assertEqual(lib.hand(h['id'],key)['pot'],99)
        self.assertEqual(hashlib.sha256(db.read_bytes()).digest(),before)
        save_analysis(db,'s','source',[h,hand(1)],{})
        self.assertEqual(lib.results(key)['total'],2)

    def test_empty_library_does_not_create_database_and_can_refresh(self):
        lib=Library(self.root)
        self.assertEqual(lib.search('Mira'),[])
        self.assertFalse((self.root/'players.sqlite3').exists())
        save_analysis(self.root/'players.sqlite3','s','source',[hand()],{})
        self.assertEqual(lib.search('Mira')[0]['count'],1)

    def test_evidence_paths_are_limited_to_session_frames(self):
        frames=self.root/'s'/'frames';frames.mkdir(parents=True)
        (frames/'ok.png').write_bytes(b'example')
        (self.root/'private.png').write_bytes(b'private')
        h=hand();h['street_evidence']={'turn':['frames/ok.png','../private.png',str(self.root/'private.png'),'frames/ok.png']}
        lib=Library(self.root,[record(h,'s')]);lib.refresh()
        self.assertEqual(lib.evidence(h['id'],'turn'),[frames/'ok.png'])
        lib=Library(self.root,[record(h,'../outside')]);lib.refresh()
        self.assertEqual(lib.evidence(h['id'],'turn'),[])

    def test_http_access_and_assets(self):
        server=AppServer(Library(self.root,[record(hand())]),token='test-only')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def get(path,headers=None):
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                conn.request('GET',path,headers=headers or {})
                response=conn.getresponse()
                return response.status,dict(response.getheaders()),response.read()
            finally:conn.close()
        try:
            status,headers,body=get('/')
            self.assertEqual(status,200);self.assertIn(b'Search a player',body)
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
            self.assertEqual(get('/app.js')[1]['Content-Type'],'text/javascript; charset=utf-8')
            self.assertEqual(get('/api/search?q=Mira')[0],403)
            self.assertEqual(get('/api/search?q=Mira',{'X-Ada-Token':'test-only','Origin':'https://outside.example'})[0],403)
            self.assertEqual(get('/',{'Host':'outside.example'})[0],403)
            auth={'X-Ada-Token':'test-only'}
            self.assertEqual(json.loads(get('/api/search',auth)[2]),[])
            self.assertEqual(json.loads(get('/api/search?q=Mira',auth)[2])[0]['count'],1)
            self.assertEqual(get('/api/hand?id=example:0&player=bad',auth)[0],404)
            self.assertEqual(get('/api/hands?player=M1ra11&page=bad',auth)[0],400)
            self.assertEqual(get('/api/evidence?id=example:0&player=M1ra11&street=turn',auth)[0],404)
            self.assertEqual(get('/../private.txt',auth)[0],404)
        finally:server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':unittest.main()
