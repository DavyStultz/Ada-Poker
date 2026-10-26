"""ClubGG seven-seat layout reader. Unknown values stay unknown.

Regions cover the original table and the Join Waiting spectator client.
Coordinates scale proportionally; other layouts still need calibration.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re

NAME_BOXES = [(435,198,610,235),(803,198,981,235),(1178,388,1360,428),
              (1035,796,1207,834),(595,879,820,922),(193,796,371,834),(56,388,239,428)]
STACK_BOXES = [(435,234,610,269),(803,234,981,269),(1178,427,1360,464),
               (1035,834,1207,873),(595,922,820,964),(193,834,371,873),(56,427,239,464)]
ACTION_BOXES = [(450,148,598,198),(816,148,968,198),(1195,340,1348,391),
                (1048,748,1202,799),(610,828,807,877),(196,748,359,799),(63,340,223,391)]
BET_BOXES = [(474,298,584,353),(825,298,939,353),(1070,495,1180,550),
             (952,639,1070,689),(650,675,763,726),(351,639,462,689),(236,495,340,550)]
PAYOUT_BOXES = [(437,35,630,99),(801,35,990,99),(1180,223,1390,294),
                (1030,624,1220,705),(600,700,813,763),(180,624,400,705),(40,223,245,294)]
ACTIONS = {"fold":"fold", "check":"check", "call":"call", "bet":"bet", "raise":"raise",
           "allin":"all_in", "all-in":"all_in", "win":"win"}

# The spectator client includes its title bar and uses different seat panels.
# These are geometry only: identities always come from local OCR.
OBSERVER_LAYOUT = 'clubgg_observer_v1'
OBSERVER_NAMES = [(444,225,604,256),(815,225,976,256),(1188,405,1349,437),
                  (1050,802,1216,837),(595,879,820,922),(203,802,365,837),(65,405,229,437)]
OBSERVER_STACKS = [(444,257,604,289),(815,257,976,289),(1188,438,1349,469),
                   (1050,838,1216,873),(595,922,820,964),(203,838,365,873),(65,438,229,469)]
OBSERVER_ACTIONS = [(444,185,604,224),(815,185,976,224),(1188,373,1349,404),
                    (1050,754,1216,800),(610,828,807,877),(203,754,365,800),(65,373,229,404)]


def name_boxes(layout=None):
    return OBSERVER_NAMES if layout == OBSERVER_LAYOUT else NAME_BOXES


def bb_amount(text):
    """Parse only explicit BB amounts; repairs apply to numbers, never names."""
    compact = re.sub(r"\s+", "", text).upper().replace(",", "")
    match = re.fullmatch(r"([+]?[0-9ILO|]+(?:\.[0-9ILO|]+)?)(?:BB|8B|B8|88)", compact)
    if not match:
        return None
    value = match[1].translate(str.maketrans({"I":"1","L":"1","|":"1","O":"0"}))
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 1000000 else None
    except ValueError:
        return None


def in_box(words, box):
    x1,y1,x2,y2 = box
    selected = [w for w in words if x1 <= w["x"]+w["width"]/2 <= x2 and y1 <= w["y"]+w["height"]/2 <= y2]
    return " ".join(w["text"] for w in sorted(selected, key=lambda w:(round(w["y"]/15),w["x"])))


def read_frame(raw, time, evidence):
    sx,sy = 1418/raw["width"],1024/raw["height"]
    words = [{**w,"x":w["x"]*sx,"y":w["y"]*sy,"width":w["width"]*sx,"height":w["height"]*sy} for w in raw["words"]]
    observer = in_box(words,(1020,910,1408,1018)).lower() == 'join waiting'
    layout = OBSERVER_LAYOUT if observer else 'clubgg_1418x1024'
    names = name_boxes(layout)
    stacks = OBSERVER_STACKS if observer else STACK_BOXES
    actions = OBSERVER_ACTIONS if observer else ACTION_BOXES
    seats = []
    for i in range(7):
        name = in_box(words,names[i]).strip()
        if name.lower() in ("", "empty", "sit", "sit here"):
            name = None
        action_text = in_box(words,actions[i]).lower().replace("all in", "all-in")
        action = next((value for key,value in ACTIONS.items() if key in action_text.split()),None)
        payout = in_box(words,PAYOUT_BOXES[i])
        seats.append({"seat":i,"username":name,"raw_username":name,
                      "stack_bb":bb_amount(in_box(words,stacks[i])),
                      "bet_bb":bb_amount(in_box(words,BET_BOXES[i])),
                      "action":action,"has_cards":None,
                      "payout_bb":bb_amount(payout) if "+" in payout else None})
    pot_text = in_box(words,(615,351,800,403))
    pot = bb_amount(re.sub(r".*?pot\s*", "", pot_text, flags=re.I))
    # Community cards cover several central felt samples at showdown. Keep
    # those frames when the surrounding seat stacks still establish the table.
    visible = raw.get("felt_points",0) >= 1 and sum(s["stack_bb"] is not None for s in seats) >= 2
    scores = raw.get("dealer_scores",[0]*7)
    order = sorted(range(7),key=lambda i:scores[i],reverse=True)
    dealer = order[0] if scores[order[0]] >= 30 and scores[order[0]]-scores[order[1]] >= 15 else None
    issues = ["card_recognition_unavailable", "ocr_values_require_review"]
    if not visible:
        issues = ["table_not_visible"]
        seats = []
        pot = dealer = None
    elif dealer is None:
        issues.append("dealer_unknown")
    return {"time":time,"evidence":evidence,"table_visible":visible,"dealer_seat":dealer,"layout":layout,
            "pot_bb":pot,"board":None,"hero_cards":None,"seats":seats,"issues":issues}


def stabilize_names(observations):
    """Replace isolated OCR glitches only with a repeated exact nearby reading.

    Never fuzzy-merge names. A newly joined player's spelling must repeat before
    it replaces the previous identity. Missing names remain missing.
    """
    segments=[]
    segment=0
    for i,o in enumerate(observations):
        if not o['table_visible'] or i and o['time']-observations[i-1]['time']>.5:
            segment+=1
        segments.append(segment)
    for seat in range(7):
        readings = [(i, next((s for s in o["seats"] if s["seat"]==seat),None)) for i,o in enumerate(observations)]
        for pos,(index,s) in enumerate(readings):
            if s is None:
                continue
            s['username_status']='unknown'
            if not s.get('raw_username'):
                continue
            nearby = Counter(item.get("raw_username") for j,item in readings[max(0,pos-5):pos+6]
                             if item and item.get("raw_username") and segments[j]==segments[index]
                             and abs(observations[j]['time']-observations[index]['time'])<=1)
            s['username_support_frames']=nearby[s['raw_username']]
            s['username_status']='conflicting_readings' if len(nearby)>1 else 'repeated_reading' if nearby[s['raw_username']]>=2 else 'single_reading'
            s["username_candidates"] = sorted(nearby)
            if len(nearby)>1:
                observations[index]["issues"].append(f"seat_{seat}_name_ambiguous")
            # Do not rewrite any different name: candidate spellings remain for
            # manual review, and only repeated exact readings become identities.
            if nearby[s["raw_username"]] < 2:
                s["username"] = None
    return observations

