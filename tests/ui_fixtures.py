"""Synthetic UI examples; no recordings or collected player information."""
from copy import deepcopy


def hand(index=0, name='Mira11'):
    names=[name,'Rowan','Cedar','Nova','Hero','Oak','RiverFox']
    positions=['UTG','UTG+1','MP','CO','BTN','SB','BB']
    seats=[{'seat':i,'username':n,'position':positions[i],'stack_bb':100+i*2.5}
           for i,n in enumerate(names)]
    events=[{'seat':5,'action':'small_blind','amount_bb':.5},
            {'seat':6,'action':'big_blind','amount_bb':1},
            {'seat':0,'street':'preflop','action':'raise','to_bb':2},
            {'seat':1,'street':'preflop','action':'fold'},
            {'seat':2,'street':'preflop','action':'raise','to_bb':6},
            {'seat':3,'street':'preflop','action':'fold'},
            {'seat':4,'street':'preflop','action':'call','amount_bb':6},
            {'seat':5,'street':'preflop','action':'fold'},
            {'seat':6,'street':'preflop','action':'fold'},
            {'seat':0,'street':'preflop','action':'raise','to_bb':18},
            {'seat':2,'street':'preflop','action':'call','amount_bb':12},
            {'seat':4,'street':'preflop','action':'call','amount_bb':12},
            {'seat':0,'street':'flop','action':'bet','amount_bb':12},
            {'seat':2,'street':'flop','action':'fold'},
            {'seat':4,'street':'flop','action':'raise','to_bb':30},
            {'seat':0,'street':'flop','action':'call','amount_bb':18},
            {'seat':0,'street':'turn','action':'check'},
            {'seat':4,'street':'turn','action':'check'},
            {'seat':0,'street':'river','action':'bet','amount_bb':24},
            {'seat':4,'street':'river','action':'call','amount_bb':24},
            {'seat':0,'street':'river','action':'show','cards':['As','Ah']},
            {'seat':4,'street':'river','action':'show','cards':['Ks','Kh']},
            {'seat':0,'street':'river','action':'win','amount_bb':163.5}]
    return {'id':f'example:{index}','table_name':'Evening table','start_time':index*60,
            'dealer_seat':4,'seats':seats,'events':events,'needs_review':True,
            'hero_cards':['Ks','Kh'],'board':['Ad','8h','Tc','3s','Qc'],'pot_bb':163.5,
            'street_pots':{'blinds':1.5,'preflop':55.5,'flop':115.5,'turn':115.5,'river':163.5}}


def record(h, session='example-session'):
    return {'session_id':session,'hand':deepcopy(h)}


def examples():
    first=hand()
    first['seats'][0].update(hole_cards=['As','Ah'],showdown=True)
    uncertain=hand(1,'Mirall')
    uncertain['certainty']={'turn':False}
    uncertain['street_evidence']={'turn':[{'image':'frames/example.png'}]}
    uncertain['seats'][0]['hole_cards']=['9s','9h']
    rest=[]
    for i in range(2,34):
        h=hand(i)
        h['events']=[{'seat':0,'street':'preflop','action':'fold'}]
        h['board']=[]
        h['pot_bb']=None
        rest.append(record(h))
    return [record(first),record(uncertain),*rest]
