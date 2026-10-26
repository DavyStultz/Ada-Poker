"""Conservative card-glyph matching for the calibrated ClubGG layout.

Templates contain small rank/suit silhouettes, not screenshots or player data.
Unseen glyphs and weak/ambiguous matches remain unknown. This is not a general
card recognizer for arbitrary themes or rotated/occluded cards.
"""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
import threading

from .reading import OBSERVER_LAYOUT

WIDTH, HEIGHT = 1418, 1024


def image_pixels(path, ffmpeg):
    result = subprocess.run([ffmpeg,"-v","error","-nostdin","-i",str(path),
        "-vf",f"scale={WIDTH}:{HEIGHT}","-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","-"],
        capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or len(result.stdout) != WIDTH*HEIGHT*3:
        raise RuntimeError("Could not decode card evidence: "+result.stderr.decode(errors="replace")[-500:])
    return result.stdout


def glyph(pixels, box, background, size=(24,36), largest=False, ignore_boxes=()):
    x1,y1,x2,y2 = box
    threshold = background * .55
    points = {(x,y) for y in range(y1,y2) for x in range(x1,x2)
              if pixels[(y*WIDTH+x)*3+1] < threshold and not any(
                  a <= x < c and b <= y < d for a,b,c,d in ignore_boxes)}
    # Keep substantial connected components; isolated antialiasing/noise is not
    # a glyph. Rank '10' deliberately retains two components.
    components=[]
    while points:
        seed=points.pop(); component={seed}; pending=[seed]
        while pending:
            x,y=pending.pop()
            for neighbor in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if neighbor in points:
                    points.remove(neighbor);component.add(neighbor);pending.append(neighbor)
        if len(component)>=8:
            components.append(component)
    if not components:
        return None
    if largest:
        components = [max(components,key=len)]
    points=set().union(*components)
    left,right=min(x for x,y in points),max(x for x,y in points)
    top,bottom=min(y for x,y in points),max(y for x,y in points)
    width,height=right-left+1,bottom-top+1
    if width<4 or height<8:
        return None
    sw,sh=size
    bits="".join("1" if (left+min(width-1,int((x+.5)*width/sw)),
                           top+min(height-1,int((y+.5)*height/sh))) in points else "0"
                 for y in range(sh) for x in range(sw))
    return {"bits":bits,"aspect":round(width/height,4),"bounds":[left,top,right+1,bottom+1]}


def match_glyph(candidate, templates, minimum=.90, margin=.04):
    if candidate is None:
        return None,0.0
    scores={}
    for template in templates:
        a,b=candidate['bits'],template['bits']
        intersection=sum(x==y=='1' for x,y in zip(a,b))
        total=a.count('1')+b.count('1')
        overlap=2*intersection/total if total else 0
        shape=math.exp(-abs(math.log(candidate['aspect']/template['aspect'])))
        score=overlap*shape
        scores[template['label']]=max(scores.get(template['label'],0),score)
    ordered=sorted(scores.items(),key=lambda pair:pair[1],reverse=True)
    if not ordered:
        return None,0.0
    label,score=ordered[0]
    runner=ordered[1][1] if len(ordered)>1 else 0
    return (label if score>=minimum and score-runner>=margin else None),round(score,4)


def board_top(pixels,x):
    for y in range(372,437):
        samples=[pixels[((y+dy)*WIDTH+x+dx)*3:((y+dy)*WIDTH+x+dx)*3+3]
                 for dx in (52,60,68) for dy in (0,2,4)]
        if all(min(c)>65 and max(c)-min(c)<16 for c in samples):
            return y
    return None


def read_board(pixels, templates):
    candidates=[]
    for slot,x in enumerate((437,546,654,763,871)):
        y=board_top(pixels,x)
        if y is None:
            candidates.append({"slot":slot,"present":False,"card":None})
            continue
        background=pixels[((y+10)*WIDTH+x+65)*3+1]
        rank,rank_score=match_glyph(glyph(pixels,(x+7,y+5,x+44,y+45),background),templates['ranks'])
        suit,suit_score=match_glyph(glyph(pixels,(x+5,y+47,x+44,y+83),background,largest=True),templates['suits'])
        candidates.append({"slot":slot,"present":True,"card":rank+suit if rank and suit else None,
                           "rank":rank,"suit":suit,"rank_score":rank_score,"suit_score":suit_score})
    present=[c for c in candidates if c['present']]
    board=[] if not present else None
    if len(present) in (3,4,5) and all(c['card'] for c in present) and [c['slot'] for c in present]==list(range(len(present))):
        values=[c['card'] for c in present]
        if len(set(values))==len(values):
            board=values
    return {"board":board,"board_candidates":candidates,"hero_cards":None}


def hero_top(pixels, samplex):
    for y in range(740,808):
        colors=[pixels[((y+dy)*WIDTH+samplex+dx)*3:((y+dy)*WIDTH+samplex+dx)*3+3]
                for dx in (0,4,8) for dy in (0,4,8,12)]
        if all(min(c)>65 and max(c)-min(c)<16 for c in colors):
            return y
    return None


def hero_glyphs(pixels, side):
    samplex,left,right = [(661,622,673),(748,693,744)][side]
    y=hero_top(pixels,samplex)
    if y is None:
        return None,None
    background=pixels[((y+12)*WIDTH+samplex)*3+1]
    rank_box=(left,y+9,right,y+47) if side==0 else (left,y+4,right,y+40)
    suit_box=(625,y+48,665,y+80) if side==0 else (693,y+42,741,y+77)
    # Exclude the badge itself; a raised card's suit can still be readable.
    # Heavily occluded shapes fail matching instead of becoming badge digits.
    suit=glyph(pixels,suit_box,background,largest=True,
               ignore_boxes=((599,833,652,880),) if side==0 else ())
    return glyph(pixels,rank_box,background),suit


EXPOSED_CARD_ORIGINS = {0:(451,99), 1:(819,99), 2:(1196,290),
                        3:(1055,699), 5:(212,699), 6:(75,290)}
OBSERVER_CARD_ORIGINS = {0:(453,130),1:(824,130),2:(1201,317),
                         3:(1059,717),5:(209,717),6:(68,317)}


def exposed_glyphs(pixels, seat, side, layout=None):
    """Small face-up cards at opponent seats; never infer card backs."""
    origins = OBSERVER_CARD_ORIGINS if layout == OBSERVER_LAYOUT else EXPOSED_CARD_ORIGINS
    x,y = origins[seat]
    x += 60*side
    samples = [pixels[((y+dy)*WIDTH+x+dx)*3+1]
               for dx in (40,43,46) for dy in (12,15,18)]
    if min(samples)<65 or max(samples)-min(samples)>18:
        return None,None
    background=sum(samples)/len(samples)
    rank_box=(x+7,y+9,x+35,y+41) if side==0 else (x+3,y+3,x+35,y+39)
    suit_box=(x+3,y+40,x+39,y+68) if side==0 else (x+1,y+37,x+41,y+66)
    return (glyph(pixels,rank_box,background),
            glyph(pixels,suit_box,background,largest=True))


def show_button_glyphs(pixels, side):
    """The folded-hand Show controls repeat hero cards without the level badge."""
    x = (945,1129)[side]
    samples=[pixels[((950+dy)*WIDTH+x+dx)*3+1] for dx in (8,20,35) for dy in (0,2)]
    if min(samples)<220 or max(samples)-min(samples)>15:
        return None,None
    return (glyph(pixels,(x+6,954,x+35,990),240),
            glyph(pixels,(x+34,954,x+64,989),240,largest=True))


def pair_reading(pixels, templates, extractor):
    candidates=[]
    for side in (0,1):
        rank_glyph,suit_glyph=extractor(pixels,side)
        rank,rs=match_glyph(rank_glyph,templates['ranks'])
        suit,ss=match_glyph(suit_glyph,templates['suits'])
        candidates.append({'slot':side,'card':rank+suit if rank and suit else None,
                           'rank':rank,'suit':suit,'rank_score':rs,'suit_score':ss})
    values=[c['card'] for c in candidates]
    return (values if all(values) and len(set(values))==2 else None),candidates


def read_cards(pixels, templates, layout=None):
    result=read_board(pixels,templates)
    # The bottom seat is another player in spectator mode, never the hero.
    hero,shown,candidates,show_candidates=None,None,[],[]
    if layout != OBSERVER_LAYOUT:
        hero,candidates=pair_reading(pixels,templates,hero_glyphs)
        shown,show_candidates=pair_reading(pixels,templates,show_button_glyphs)
    result['card_issues']=[]
    if layout == OBSERVER_LAYOUT:
        result['card_issues'].append('observer_bottom_hole_cards_uncalibrated')
    if hero and shown and hero!=shown:
        result['card_issues'].append('hero_card_sources_conflict')
        hero=None
    elif not hero and shown:
        # Partial main-card readings must also agree with the repeated controls.
        if any(c['rank'] and c['rank']!=v[0] or c['suit'] and c['suit']!=v[1]
               for c,v in zip(candidates,shown)):
            result['card_issues'].append('hero_card_sources_conflict')
        else:
            hero=shown
    result['hero_cards']=hero
    result['hero_card_source']='show_controls' if hero and shown else 'main_cards' if hero else None
    result['hero_show_candidates']=show_candidates
    if result['hero_cards'] and result['board'] and set(result['hero_cards']) & set(result['board']):
        result['card_issues'].append('duplicate_cards_across_seats')
        result['hero_cards']=None
    result['hero_candidates']=candidates
    result['exposed_cards']=[]
    for seat in EXPOSED_CARD_ORIGINS:
        cards,details=pair_reading(pixels,templates,lambda p,side:exposed_glyphs(p,seat,side,layout))
        result['exposed_cards'].append({'seat':seat,'cards':cards,'candidates':details})
    # Reject impossible duplicates across any recognized complete card groups.
    groups=[result['board'],result['hero_cards']]+[s['cards'] for s in result['exposed_cards']]
    all_cards=[card for group in groups if group for card in group]
    duplicates={card for card in all_cards if all_cards.count(card)>1}
    if duplicates:
        result['card_issues'].append('duplicate_cards_across_seats')
        if result['hero_cards'] and duplicates.intersection(result['hero_cards']):result['hero_cards']=None
        for s in result['exposed_cards']:
            if s['cards'] and duplicates.intersection(s['cards']):s['cards']=None
    return result


def load_templates(layout=None):
    filename = 'card_templates_observer.json' if layout == OBSERVER_LAYOUT else 'card_templates.json'
    return json.loads(Path(__file__).with_name(filename).read_text(encoding='utf-8'))


def image_sequence(paths, ffmpeg):
    """Decode cached JPEGs through one bounded pipe instead of one process/image."""
    if not paths:
        return
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen([ffmpeg,'-hide_banner','-loglevel','error','-threads','1',
            '-f','image2pipe','-framerate','5','-vcodec','mjpeg','-i','pipe:0',
            '-vf',f'scale={WIDTH}:{HEIGHT}','-threads','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,creationflags=subprocess.CREATE_NO_WINDOW)
        feed_errors=[]
        def feed():
            try:
                for path in paths:
                    process.stdin.write(Path(path).read_bytes())
            except (OSError,ValueError) as exc:
                feed_errors.append(exc)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass
        writer=threading.Thread(target=feed,daemon=True);writer.start()
        try:
            for _ in paths:
                data=process.stdout.read(WIDTH*HEIGHT*3)
                if len(data)!=WIDTH*HEIGHT*3:
                    raise RuntimeError('Card analysis could not decode every evidence frame.')
                yield data
            if process.wait(timeout=30) or feed_errors:
                errors.seek(0)
                raise RuntimeError('Card decoding failed: '+errors.read().decode(errors='replace')[-1000:])
        finally:
            if process.poll() is None:
                process.kill();process.wait(timeout=10)
            process.stdout.close();writer.join(timeout=5)


def add_card_readings(observations, output, ffmpeg, source_hash, progress=print):
    profiles={layout:load_templates(layout) for layout in dict.fromkeys(o.get('layout') for o in observations)}
    key=hashlib.sha256(json.dumps({'engine':6,'templates':list(profiles.values()),'source':source_hash,
        'frames':[(o['time'],o['table_visible'],o['evidence']['image'],o.get('layout')) for o in observations]},sort_keys=True).encode()).hexdigest()
    cache_path=Path(output)/'cards-cache.json'
    cache=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    selected=[(i,o) for i,o in enumerate(observations) if o['table_visible']]
    if cache.get('key')==key and len(cache.get('readings',[]))==len(selected):
        readings=cache['readings']
        progress('Reusing local card-reading cache.')
    else:
        progress(f'Reading card shapes in {len(selected)} visible-table frames...')
        readings=[]
        paths=[Path(output)/o['evidence']['image'] for _,o in selected]
        for count,pixels in enumerate(image_sequence(paths,ffmpeg),1):
            layout=selected[count-1][1].get('layout')
            readings.append(read_cards(pixels,profiles[layout],layout))
            if count%100==0 or count==len(paths):
                progress(f'Read card shapes {count}/{len(paths)}')
        temporary=cache_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'key':key,'readings':readings}),encoding='utf-8')
        temporary.replace(cache_path)
    for (index,o),reading in zip(selected,readings):
        o.update(reading)
        o['issues']=[issue for issue in o['issues'] if issue!='card_recognition_unavailable']
        o['issues'].append('card_templates_limited_coverage')
        o['issues'].extend(reading.get('card_issues',[]))
    # A one-frame match during animations is not enough. Do not carry a card
    # across a missing interval, and never backfill cards into the actual video.
    for field in ('board','hero_cards'):
        values=[o.get(field) for o in observations]
        for i,o in enumerate(observations):
            value=values[i]
            if value and not any(0<=j<len(values) and values[j]==value and
                    abs(observations[j]['time']-o['time'])<.5 for j in (i-1,i+1)):
                o[field]=None
                o['issues'].append('card_match_not_repeated')
    add_visible_hole_cards(observations)
    return observations


def add_visible_hole_cards(observations):
    """Retain independently repeated rank/suit parts, never concealed cards."""
    partial=[]
    for o in observations:
        groups={}
        if o.get('table_visible') and 'duplicate_cards_across_seats' not in o.get('card_issues',[]):
            for item in o.get('exposed_cards',[]):
                values=[c.get('card') for c in item['candidates']]
                if not all(values) or len(set(values))==len(values):
                    groups[item['seat']]=item['candidates']
            if o.get('layout') != OBSERVER_LAYOUT and 'hero_card_sources_conflict' not in o.get('card_issues',[]):
                groups[4]=[{'rank':c[0],'suit':c[1]} for c in o['hero_cards']] if o.get('hero_cards') else o.get('hero_candidates',[])
        partial.append(groups)
    for i,o in enumerate(observations):
        o['visible_hole_cards']=[]
        for seat,candidates in partial[i].items():
            # A suit-like fragment on a card back/avatar is not face-up evidence.
            if not any(c.get('rank') for c in candidates):
                continue
            cards=[]
            for slot,candidate in enumerate(candidates):
                card={}
                for feature in ('rank','suit'):
                    value=candidate.get(feature)
                    supported=any(0<=j<len(observations) and abs(observations[j]['time']-o['time'])<.5
                        and len(partial[j].get(seat,[]))>slot
                        and partial[j][seat][slot].get(feature)==value for j in (i-1,i+1))
                    card[feature]=value if value and supported else None
                cards.append(card)
            if any(c['rank'] or c['suit'] for c in cards):
                o['visible_hole_cards'].append({'seat':seat,'cards':cards,'evidence':o.get('evidence')})
