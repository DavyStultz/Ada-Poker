"""Read-only presentation adapter for the player history interface.

The UI accepts supplied records as correct unless explicit certainty says
otherwise. It does not rewrite legacy reconstruction/verification metadata.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import json
import math
import sqlite3
import threading

STREETS = ('blinds','preflop','flop','turn','river')
LOOKALIKES = str.maketrans({c:'1' for c in '1iIlL'})


def name_key(name):
    return (name or '').strip().translate(LOOKALIKES)


def certain(value):
    return value is not False and value not in ('uncertain','unknown')


def money(value):
    try:
        result=float(value)
        return result if math.isfinite(result) and result>=0 else None
    except (TypeError,ValueError):
        return None


def card_parts(seat):
    if seat.get('hole_cards'):
        return list(seat['hole_cards'])
    return [(p.get('rank') or '?')+(p.get('suit') or '?') for p in seat.get('hole_card_parts',[])]


def position_group(hand, seat):
    if seat.get('position_group') in ('blinds','early','late'):
        return seat['position_group']
    if seat.get('position') in ('SB','BB'):
        return 'blinds'
    dealer=hand.get('dealer_seat')
    if dealer is None or any(not s.get('position') for s in hand.get('seats',[])):
        return None
    seats=sorted(hand.get('seats',[]),key=lambda s:(s['seat']-dealer-1)%7)
    nonblinds=[s['seat'] for s in seats if s.get('position') not in ('SB','BB')]
    if seat['seat'] not in nonblinds:
        return None
    return 'early' if nonblinds.index(seat['seat'])<2 else 'late'


def street_certain(hand, street):
    value=hand.get('certainty',{}).get(street,True)
    if isinstance(value,dict):value=value.get('certain',True)
    return certain(value)


def identity_certain(hand, seat):
    if not seat.get('username'):return False
    if certain(hand.get('certainty',{}).get('username',True)) and certain(seat.get('username_certain',True)):
        return True
    variants=set(seat.get('username_candidates',[])) | {seat.get('username')}
    variants.discard(None)
    # The user permits the narrow look-alike ambiguity, not an unknown identity.
    return len(variants)>1 and len({name_key(n) for n in variants})==1


def public_hand(raw, session_id):
    seats=[]
    for seat in raw.get('seats',[]):
        seats.append({'seat':seat['seat'],'name':seat.get('username') or 'Unknown player',
            'key':name_key(seat.get('username')),'position':seat.get('position'),
            'group':position_group(raw,seat),'stack':money(seat.get('stack_bb')),
            'cards':card_parts(seat),'identity_certain':identity_certain(raw,seat),
            'showdown':seat.get('reveal_type')=='showdown' or seat.get('showdown') is True})
    if raw.get('hero_cards'):
        for seat in seats:
            if seat['seat']==4:seat['cards']=raw['hero_cards']
    lookup={s['seat']:s for s in seats}
    events=[]
    ordinals=Counter()
    for index,event in enumerate(raw.get('events',[])):
        action=event.get('action','unknown')
        street=event.get('street','unknown')
        if action in ('small_blind','big_blind','ante','straddle','sb','bb'):street='blinds'
        if street not in STREETS:street='unknown'
        is_raise=action in ('raise','reraise') or event.get('is_raise') is True
        ordinal=None
        if is_raise:
            ordinals[street]+=1
            ordinal=event.get('raise_order') or ordinals[street]
            ordinals[street]=max(ordinals[street],ordinal)
        seat=lookup.get(event.get('seat'),{})
        events.append({'index':index,'seat':event.get('seat'),
            'name':seat.get('name') or event.get('username') or 'Unknown player',
            'key':seat.get('key') or name_key(event.get('username')),
            'position':seat.get('position'),'action':action,'street':street,
            'amount':money(event.get('amount_bb')),
            'to':money(event.get('raise_to_bb',event.get('to_bb'))),
            'stack':money(event.get('stack_after_bb')),'raise_order':ordinal,
            'reraise':is_raise and (ordinal>1 or action=='reraise'),
            'cards':event.get('cards') or [],'time':event.get('time')})
    streets={s:{'certain':street_certain(raw,s),'pot':money(raw.get('street_pots',{}).get(s)),
                'events':[e for e in events if e['street']==s]} for s in STREETS}
    return {'id':raw['id'],'session_id':session_id,'title':raw.get('table_name') or 'Recorded hand',
            'time':raw.get('start_time',0),'seats':seats,'events':events,'streets':streets,
            'board':raw.get('board') or [],'dealer':raw.get('dealer_seat'),
            'pot':money(raw.get('pot_bb')),'preflop_certain':street_certain(raw,'preflop')}


class Library:
    def __init__(self, analysis_root, records=None):
        self.root=Path(analysis_root).resolve()
        self.fixture=deepcopy(records) if records is not None else None
        self.lock=threading.RLock()
        self.stamp=None
        self.raw={}
        self.hands={}
        self.players={}

    def refresh(self):
        with self.lock:
            db=self.root/'players.sqlite3'
            purposes_path=self.root/'session-purposes.json'
            stamp=tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None
                        for p in (db,Path(str(db)+'-wal'),purposes_path))
            if self.stamp==stamp:return
            if self.fixture is not None:
                records=self.fixture
            elif not db.exists():
                records=[]
            else:
                purposes=json.loads(purposes_path.read_text(encoding='utf-8')) if purposes_path.exists() else {}
                if not isinstance(purposes,dict):raise ValueError('Invalid session purposes file.')
                conn=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
                try:
                    if conn.execute('PRAGMA user_version').fetchone()[0] not in (1,2):
                        raise ValueError('This player database version is not supported.')
                    records=[]
                    for payload,session,patch in conn.execute('SELECT h.json,h.session_id,c.patch_json FROM hands h LEFT JOIN corrections c ON c.hand_id=h.id AND c.session_id=h.session_id'):
                        if purposes.get(session)=='test':continue
                        raw=json.loads(payload)
                        if patch:raw.update(json.loads(patch))
                        records.append({'session_id':session,'hand':raw})
                finally:conn.close()
            self.raw={r['hand']['id']:r for r in records}
            self.hands={r['hand']['id']:public_hand(r['hand'],r['session_id']) for r in records}
            groups=defaultdict(lambda:{'aliases':Counter(),'ids':set()})
            for h in self.hands.values():
                if not h['preflop_certain'] or not any(s['seat']==4 and s['key'] for s in h['seats']):continue
                if any(not s['identity_certain'] for s in h['seats']):continue
                for s in h['seats']:
                    if s['seat']==4 or not s['key'] or not s['identity_certain']:continue
                    groups[s['key']]['aliases'][s['name']]+=1
                    groups[s['key']]['ids'].add(h['id'])
            self.players={key:{'key':key,'name':g['aliases'].most_common(1)[0][0],
                'aliases':sorted(g['aliases']),'count':len(g['ids']),'ids':g['ids']} for key,g in groups.items()}
            self.stamp=stamp

    def search(self, query):
        with self.lock:
            self.refresh()
            needle=name_key(query).casefold()
            if not needle:return []
            matches=[{k:v for k,v in p.items() if k!='ids'} for p in self.players.values()
                     if needle in p['key'].casefold()]
            return sorted(matches,key=lambda p:(p['key'].casefold()!=needle,p['name'].casefold()))[:20]

    def hand(self, hand_id, player):
        self.refresh()
        if player not in self.players or hand_id not in self.players[player]['ids']:
            raise KeyError('Hand not found for this player.')
        result=deepcopy(self.hands[hand_id])
        result['player']=player
        for street in STREETS:
            result['streets'][street]['images']=len(self.evidence(hand_id,street))
        return result

    def evidence(self, hand_id, street):
        record=self.raw.get(hand_id)
        if not record:return []
        raw=record['hand']
        supplied=raw.get('street_evidence',{}).get(street,[])
        if isinstance(supplied,dict):supplied=[supplied]
        paths=list(supplied)
        paths.extend(e.get('evidence') for e in raw.get('events',[]) if e.get('street')==street)
        seen=[]
        folder=(self.root/record['session_id']).resolve()
        if folder.parent!=self.root:return []
        frames=(folder/'frames').resolve()
        if frames.parent!=folder:return []
        for evidence in paths:
            image=evidence.get('image') if isinstance(evidence,dict) else evidence
            if not image:continue
            path=(folder/image).resolve()
            if path.is_relative_to(frames) and path.suffix.lower() in ('.jpg','.jpeg','.png') and path.is_file() and path not in seen:
                seen.append(path)
        return seen

    def results(self, player, filters=None, page=0):
        with self.lock:
            self.refresh()
            filters=filters or {}
            profile=self.players.get(player)
            if profile is None:return {'player':None,'hands':[],'total':0,'page':0,'pages':0}
            rows=[]
            for hand_id in profile['ids']:
                h=self.hands[hand_id]
                targets=[s for s in h['seats'] if s['key']==player and s['seat']!=4 and s['identity_certain']]
                if not targets:continue
                cards=max((s['cards'] for s in targets),key=lambda cs:sum(c!='??' for c in cs))
                revealed=any(c!='??' for c in cards)
                showdown=any(s['showdown'] for s in targets)
                if filters.get('cards')=='seen' and not revealed:continue
                if filters.get('cards')=='unseen' and revealed:continue
                if filters.get('cards')=='showdown' and not (showdown and revealed):continue
                if filters.get('position') and not any(s['group']==filters['position'] for s in targets):continue
                street=filters.get('street') or 'preflop'
                action=filters.get('action')
                order=filters.get('order')
                size=money(filters.get('size')) if filters.get('size') not in (None,'') else None
                has_action_filter=bool(action or order or size is not None)
                candidates=[e for e in h['events'] if e['key']==player and e['street']==street]
                if has_action_filter:
                    if not h['streets'].get(street,{}).get('certain',False):continue
                    def matches(e):
                        if action=='reraise' and not e['reraise']:return False
                        if action=='raise' and e['raise_order'] is None:return False
                        if action not in (None,'','raise','reraise') and e['action']!=action:return False
                        if order and str(e['raise_order'])!=str(order):return False
                        value=(e['to'] if e['to'] is not None else e['amount']) if e['raise_order'] is not None else (e['amount'] if e['amount'] is not None else e['to'])
                        return size is None or value is not None and abs(value-size)<.000001
                    if not any(matches(e) for e in candidates):continue
                streets={s:{'certain':h['streets'][s]['certain'],
                    'events':[e for e in h['events'] if e['key']==player and e['street']==s]} for s in STREETS[1:]}
                rows.append({'id':h['id'],'cards':cards,'revealed':revealed,'showdown':showdown,
                    'streets':streets,'time':h['time'],'session_id':h['session_id']})
            rows.sort(key=lambda h:(h['session_id'],h['time'],h['id']),reverse=True)
            rows.sort(key=lambda h:(not h['revealed'],not h['showdown'],not all('?' not in c for c in h['cards'])))
            total=len(rows)
            pages=(total+29)//30
            page=max(0,min(int(page),max(0,pages-1)))
            return {'player':{k:v for k,v in profile.items() if k!='ids'},'hands':rows[page*30:page*30+30],
                    'total':total,'page':page,'pages':pages}
