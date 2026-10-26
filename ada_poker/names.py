"""Supplement full-table OCR with local, enlarged username crops.

No network or model API. Crop readings are evidence, not verified identities.
Only repeated exact readings can fill a missing name; disagreement is retained.
"""
import hashlib
import json
from pathlib import Path
import struct
from contextlib import contextmanager
import shutil
import uuid
import os
import stat

from .cards import WIDTH, image_sequence
from .ocr import recognize_images
from .reading import name_boxes

NAME_READER_VERSION = 2


@contextmanager
def temporary_panels(output):
    # Inherit the output folder's Windows permissions. tempfile's private 0700
    # directory ACL can exclude the restricted execution account on Windows.
    root=Path(output).resolve()
    folder=root/('.name-crops-'+uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield folder
    finally:
        if folder.resolve().parent!=root or folder.is_symlink():
            raise ValueError('Refusing cleanup outside the analysis folder.')
        def clear_readonly(operation,path,error):
            target=Path(path).resolve()
            if not isinstance(error,PermissionError) or not target.is_relative_to(folder):
                raise error
            # OneDrive can set ReadOnly on a newly created directory. This only
            # clears that attribute on our disposable crop files/folder, not ACLs.
            os.chmod(path,stat.S_IWRITE)
            operation(path)
        shutil.rmtree(folder,onexc=clear_readonly)


def name_panel(pixels, layout=None):
    """3x grayscale text on white, seven separated rows; standard local BMP."""
    width,height = 720,1120  # 24-bit BMP rows are already four-byte aligned.
    data=bytearray(b'\xff'*(width*height*3))
    for seat,(x1,y1,x2,y2) in enumerate(name_boxes(layout)):
        for y in range(y1+3,y2-3):
            row=bytearray()
            for x in range(x1,x2):
                value=255-max(pixels[(y*WIDTH+x)*3:(y*WIDTH+x)*3+3])
                row.extend(bytes([value]*9))
            for dy in range(3):
                offset=((seat*160+15+(y-y1-3)*3+dy)*width+15)*3
                data[offset:offset+len(row)]=row
    header=struct.pack('<2sIHHI',b'BM',54+len(data),0,0,54)
    header+=struct.pack('<IiiHHIIiiII',40,width,-height,1,24,0,len(data),0,0,0,0)
    return header+data


def panel_names(raw):
    names=[]
    for seat in range(7):
        # Reject stray bright borders above the text, and preserve real spaces.
        words=[w for w in raw['words'] if seat*160+26 <= w['y']+w['height']/2 < seat*160+95
               and w['height']>=20]
        value=' '.join(w['text'] for w in sorted(words,key=lambda w:w['x'])).strip()
        names.append(value if value and value.lower() not in ('empty','sit','sit here') else None)
    return names


def selected_name_frames(observations):
    """Read changes twice plus a five-second refresh; don't OCR each video frame."""
    selected=[]
    last_signature=None
    last_time=-float('inf')
    confirm=False
    for i,o in enumerate(observations):
        if not o['table_visible']:
            last_signature=None
            confirm=False
            continue
        signature=tuple((s['seat'],s.get('raw_username'),s.get('stack_bb') is not None) for s in o['seats'])
        changed=signature!=last_signature
        due=o['time']-last_time>=5
        if changed or confirm or due:
            selected.append(i)
            last_time=o['time']
            confirm=changed or due
        last_signature=signature
    return selected


def apply_name_evidence(observations, readings):
    """Attach crops to their actual frame and fill only supported missing text."""
    for index,names in readings.items():
        for seat in observations[index]['seats']:
            seat['crop_username']=names[seat['seat']]
    for i,o in enumerate(observations):
        for seat in o['seats']:
            crop=seat.get('crop_username')
            raw=seat.get('raw_username')
            candidates=set(seat.get('username_candidates',[]))
            if crop:candidates.add(crop)
            seat['username_candidates']=sorted(candidates)
            if crop and raw and crop!=raw:
                seat['username_status']='conflicting_readings'
                o['issues'].append(f"seat_{seat['seat']}_name_sources_disagree")
            if crop and not raw and seat.get('stack_bb') is not None:
                neighbors=[]
                for j in (i-1,i+1):
                    if 0<=j<len(observations) and abs(observations[j]['time']-o['time'])<.5:
                        neighbors.extend(s.get('crop_username') for s in observations[j]['seats'] if s['seat']==seat['seat'])
                if crop in neighbors:
                    seat['username']=crop
                    seat['username_source']='ocr_crop'
                    seat['username_status']='repeated_crop_reading'
    return observations


def add_name_readings(observations, output, ffmpeg, source_hash, progress=print):
    selected=selected_name_frames(observations)
    key=hashlib.sha256(json.dumps({'version':NAME_READER_VERSION,'source':source_hash,
        'frames':[(i,observations[i]['evidence']['image'],observations[i].get('layout')) for i in selected]},sort_keys=True).encode()).hexdigest()
    cache_path=Path(output)/'names-cache.json'
    cache=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    if cache.get('key')==key and len(cache.get('readings',[]))==len(selected):
        readings=cache['readings']
        progress('Reusing local username crop cache.')
    else:
        readings=[]
        progress(f'Checking enlarged username crops in {len(selected)} frames locally...')
        for start in range(0,len(selected),24):
            batch=selected[start:start+24]
            # Bounded temporary crops, removed after each batch; cache only text.
            with temporary_panels(output) as temporary:
                paths=[]
                inputs=[Path(output)/observations[i]['evidence']['image'] for i in batch]
                for n,pixels in enumerate(image_sequence(inputs,ffmpeg)):
                    path=Path(temporary)/f'{n}.bmp'
                    path.write_bytes(name_panel(pixels,observations[batch[n]].get('layout')));paths.append(path)
                readings.extend(panel_names(raw) for raw in recognize_images(paths))
            progress(f'Checked username crops {len(readings)}/{len(selected)}')
        temporary=cache_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'key':key,'readings':readings}),encoding='utf-8')
        temporary.replace(cache_path)
    return apply_name_evidence(observations,dict(zip(selected,readings)))
