"""Compare extracted histories against a local, independently checked reference.

Does not read video with a model or alter source videos, analyses, or databases.
References describe selected confirmed facts; passing is NOT proof of complete
reconstruction unless every action, boundary and accounting detail was annotated.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def compare(hands,reference):
    results=[]
    for expected in reference['hands']:
        index=expected['index'];failures=[]
        actual=hands[index] if index<len(hands) else None
        if actual is None:
            results.append({'index':index,'passed':False,'failures':['Hand missing']});continue
        for field in ('start_time','end_time'):
            if field in expected and abs(actual.get(field,-999)-expected[field])>reference.get('boundary_tolerance',.4):
                failures.append(f'{field}: expected approximately {expected[field]}, got {actual.get(field)}')
        for field in ('start_known','end_known','dealer_seat','board','game_variant'):
            if field in expected and actual.get(field)!=expected[field]:
                failures.append(f'{field}: expected {expected[field]}, got {actual.get(field)}')
        seats={s['seat']:s for s in actual.get('seats',[])}
        for seat,name in expected.get('names',{}).items():
            got=seats.get(int(seat),{}).get('username')
            if got!=name:failures.append(f'Seat {seat} username: expected {name}, got {got}')
        for seat,ranks in expected.get('revealed_ranks',{}).items():
            record=seats.get(int(seat),{})
            cards=record.get('hole_cards')
            parts=record.get('hole_card_parts') or []
            got=[c[0] for c in cards] if cards else [c.get('rank') for c in parts]
            if sorted(got,key=str)!=sorted(ranks):failures.append(f'Seat {seat} revealed ranks: expected {ranks}, got {got}')
        for event in expected.get('required_events',[]):
            matches=[e for e in actual.get('events',[]) if e.get('seat')==event['seat'] and
                     e.get('action')==event['action'] and abs(e.get('time',-999)-event['time'])<=event.get('tolerance',1)]
            if not matches:
                failures.append(f'Missing observed event: {event}');continue
            if not any(e.get('street')==event['street'] and
                       ('bb' not in event or event['bb'] in (e.get('amount_bb'),e.get('to_bb'))) for e in matches):
                failures.append(f'Wrong street/amount for {event["action"]} by seat {event["seat"]} near {event["time"]}s: {[(e.get("street"),e.get("amount_bb"),e.get("to_bb")) for e in matches]}')
        results.append({'index':index,'passed':not failures,'failures':failures})
    return {'reference_scope':reference.get('scope','selected confirmed facts'),
            'hand_count_matches':len(hands)==len(reference['hands']),
            'extracted_hands':len(hands),'reference_hands':len(reference['hands']),
            'passed_hands':sum(r['passed'] for r in results),'results':results,
            'completeness_verified':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('analysis',type=Path)
    parser.add_argument('reference',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    reference=json.loads(args.reference.read_text(encoding='utf-8'))
    summary=json.loads((args.analysis/'summary.json').read_text(encoding='utf-8'))
    if summary['source_hash']!=reference['source_hash']:parser.error('Reference belongs to a different source video.')
    report=compare(json.loads((args.analysis/'hands.json').read_text(encoding='utf-8')),reference)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(f'{report["passed_hands"]}/{report["reference_hands"]} hands satisfy the annotated checks. Full completeness remains unverified.')
    print('Report:',args.output)
    return 0 if report['hand_count_matches'] and report['passed_hands']==report['reference_hands'] else 1


if __name__=='__main__':raise SystemExit(main())
