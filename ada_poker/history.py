"""Bounded, conservative reconstruction of poker hands from OCR observations.

reconstruct() turns a time-ordered list of noisy, 5fps table observations into
a list of hand records. It never invents actions, winners, or hole cards; it
only reports what the observations support, with uncertainty preserved in
`issues` and `needs_review`. This module implements reconstruction; capture,
recognition, storage, and review are handled by the surrounding modules.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

# Seats are numbered clockwise: 0 top-left, 1 top-right, 2 right, 3 bottom-right,
# 4 hero, 5 bottom-left, 6 left. Clockwise traversal is therefore seat+1 mod 7.
SEAT_COUNT = 7

# Position naming conventions. Heads-up uses SB/BB only (dealer is SB). For
# 3-7 participants, clockwise from the button: BTN, SB, BB, then the usual
# UTG / (UTG+1) / HJ / CO late-position names filling in as seats are added.
POSITION_TABLES = {
    1: ["BTN"],
    2: ["SB", "BB"],
    3: ["BTN", "SB", "BB"],
    4: ["BTN", "SB", "BB", "CO"],
    5: ["BTN", "SB", "BB", "HJ", "CO"],
    6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
    7: ["BTN", "SB", "BB", "UTG", "UTG+1", "HJ", "CO"],
}

# Number of leading observations of a hand in which a positive bet still
# counts as evidence the seat is participating (e.g. posted blind/straddle).
EARLY_WINDOW = 3


def _clean_amount(value):
    """Return a finite, non-negative float, or None if value is unusable."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not dec.is_finite() or dec < 0:
        return None
    return float(dec)


def _make_id(session_id: str, index: int, start_time: float) -> str:
    return f"{session_id}:{index:04d}:{int(round(start_time * 1000))}"


def _new_seat_data():
    return {
        "usernames": Counter(),
        "last_username": None,
        "stack_first": None,
        "stack_last": None,
        "has_cards": False,
        "bet_early_positive": False,
        "action_seen": False,
        "last_action": None,
        "last_payout": None,
        "last_action_event": None,
        "fold_observed": False,
        "username_sources": {},
        "hole_readings": [{'rank':set(),'suit':set()} for _ in range(2)],
        "hole_card_evidence": [],
    }


class _Hand:
    def __init__(self, start_time, start_known, boundary_reason, dealer_seat):
        self.start_time = start_time
        self.last_obs_time = start_time
        self.start_known = start_known
        self.end_known = False
        self.boundary_reason = boundary_reason
        self.dealer_seat = dealer_seat
        self.seat_data = {s: _new_seat_data() for s in range(SEAT_COUNT)}
        self.events = []
        self.board = None
        self.hero_cards = None
        self.evidence = []
        self.issues = set()
        self.obs_count = 0
        self.pot_observations = []
        self.hero_readings = set()
        self.street = None

    def accumulate(self, o):
        self.last_obs_time = o["time"]
        self.obs_count += 1
        self.issues.update(o.get("issues") or [])
        pot = _clean_amount(o.get("pot_bb"))
        if pot is not None and (not self.pot_observations or self.pot_observations[-1]["pot_bb"] != pot):
            self.pot_observations.append({"time":o["time"], "pot_bb":pot, "evidence":o.get("evidence")})
        evidence = o.get("evidence")
        if evidence:
            self.evidence.append(evidence)
        board = o.get("board")
        streets={0:'preflop',3:'flop',4:'turn',5:'river'}
        if board is not None and len(board) in streets:
            new_street=streets[len(board)]
            order=['preflop','flop','turn','river']
            if self.street is None or order.index(new_street)>order.index(self.street):
                self.street=new_street
        if board:
            common=min(len(board),len(self.board or []))
            if self.board and board[:common] != self.board[:common]:
                self.issues.add('board_readings_conflict')
            if len(board)>=len(self.board or []):
                self.board = board
        hero_cards = o.get("hero_cards")
        if hero_cards:
            self.hero_readings.add(tuple(hero_cards))
            self.hero_cards = hero_cards if len(self.hero_readings)==1 else None
            if len(self.hero_readings)>1:
                self.issues.add('hero_card_readings_conflict')
        for exposed in o.get('visible_hole_cards',[]):
            seat=exposed['seat']
            if seat not in self.seat_data:
                continue
            sd=self.seat_data[seat]
            for target,card in zip(sd['hole_readings'],exposed['cards']):
                for feature in ('rank','suit'):
                    if card.get(feature):target[feature].add(card[feature])
            if any(c.get('rank') and c.get('suit') for c in exposed['cards']):
                sd['has_cards']=True
            previous=sd['hole_card_evidence']
            if not previous or previous[-1]['cards']!=exposed['cards']:
                previous.append({'time':o['time'],'cards':exposed['cards'],'evidence':o.get('evidence')})
        for seat_obs in o.get("seats") or []:
            seat = seat_obs.get("seat")
            if not isinstance(seat, int) or not (0 <= seat < SEAT_COUNT):
                continue
            before=len(self.events)
            self._accumulate_seat(o, self.seat_data[seat], seat, seat_obs)
            for event in self.events[before:]:
                event['street']=self.street or 'unknown'
                event['username_source']=seat_obs.get('username_source','ocr')

    def _accumulate_seat(self, o, sd, seat, seat_obs):
        username = seat_obs.get("username")
        if username:
            sd["usernames"][username] += 1
            sd["last_username"] = username
            sd['username_sources'].setdefault(username,set()).add(seat_obs.get('username_source','ocr'))

        raw_action = seat_obs.get("action")
        bet = _clean_amount(seat_obs.get("bet_bb"))
        stack = _clean_amount(seat_obs.get("stack_bb"))
        payout = _clean_amount(seat_obs.get("payout_bb"))

        if stack is not None:
            if sd["stack_first"] is None:
                sd["stack_first"] = stack
            if sd["stack_last"] is not None and stack != sd["stack_last"] and not raw_action:
                delta = sd["stack_last"] - stack
                issues = ["stack_change_without_action_label"]
                if delta < 0:
                    issues.append("stack_increase_without_action_label")
                self.events.append({
                    "time": o["time"], "seat": seat, "username": sd["last_username"],
                    "action": "stack_change", "amount_bb": None,
                    "stack_delta_bb": round(-delta, 4), "stack_after_bb": stack,
                    "to_bb": None, "evidence": o.get("evidence"), "origin": "observed",
                    "issues": issues,
                })
            sd["stack_last"] = stack

        if bet is not None and bet > 0 and self.obs_count <= EARLY_WINDOW:
            sd["bet_early_positive"] = True

        if seat_obs.get("has_cards"):
            sd["has_cards"] = True

        if raw_action:
            sd["action_seen"] = True
            if raw_action != sd["last_action"] and not (raw_action=='fold' and sd['fold_observed']):
                # A chip label is the total displayed commitment, not necessarily
                # the amount of this call/raise. Never assign it to fold/check.
                amount = payout if raw_action == "win" else None
                to_bb = bet if raw_action in {"call","bet","raise","all_in"} else None
                issues = [] if raw_action in {"fold","check"} or amount is not None else ["amount_unavailable"]
                if to_bb is not None:
                    issues.append("displayed_commitment_not_action_increment")
                self.events.append({
                    "time": o["time"], "seat": seat, "username": sd["last_username"],
                    "action": raw_action, "amount_bb": amount, "to_bb": to_bb,
                    "evidence": o.get("evidence"), "origin": "observed", "issues": issues,
                })
                sd['last_action_event']=self.events[-1]
                if raw_action=='fold':
                    sd['fold_observed']=True
                if raw_action == "win":
                    sd["last_payout"] = payout
            elif raw_action==sd['last_action'] and raw_action in {'call','bet','raise','all_in'} and bet is not None:
                # Chips may update a frame after the action label. Preserve the
                # later amount's own evidence without calling it the increment.
                event=sd['last_action_event']
                if event is not None and event['to_bb']!=bet:
                    event['to_bb']=bet
                    event['amount_evidence']=o.get('evidence')
                    if 'displayed_commitment_not_action_increment' not in event['issues']:
                        event['issues'].append('displayed_commitment_not_action_increment')
            sd["last_action"] = raw_action
        else:
            sd["last_action"] = None
            if payout is not None and sd["last_payout"] != payout:
                self.events.append({
                    "time": o["time"], "seat": seat, "username": sd["last_username"],
                    "action": "win", "amount_bb": payout, "to_bb": None,
                    "evidence": o.get("evidence"), "origin": "observed", "issues": [],
                })
                sd["last_payout"] = payout


def _assign_positions(hand: _Hand):
    participants = [
        s for s in range(SEAT_COUNT)
        if hand.seat_data[s]["has_cards"] or hand.seat_data[s]["action_seen"]
        or hand.seat_data[s]["bet_early_positive"]
    ]
    occupied = [s for s in range(SEAT_COUNT) if hand.seat_data[s]["usernames"]]
    positions = {}
    extra_issues = set()
    dealer_seat = hand.dealer_seat
    if dealer_seat is None:
        extra_issues.add("dealer_unknown_positions_unassigned")
    elif dealer_seat not in set(occupied) | set(participants):
        extra_issues.add("dealer_seat_not_confirmed_participant")
    elif len(set(occupied) | set(participants)) >= 2:
        # OCR cannot establish that every occupied player was dealt in. Display
        # provisional positions over occupied seats instead of silently skipping
        # players whose action labels were not captured.
        position_seats = set(occupied) | set(participants)
        extra_issues.add("positions_provisional_occupied_seats")
        ordered = []
        for offset in range(SEAT_COUNT):
            seat = (dealer_seat + offset) % SEAT_COUNT
            if seat in position_seats:
                ordered.append(seat)
        names = POSITION_TABLES.get(len(ordered))
        if names is None:
            extra_issues.add("position_assignment_uncertain")
        else:
            positions = dict(zip(ordered, names))
        if dealer_seat not in participants:
            extra_issues.add("dealer_seat_not_confirmed_participant")
    else:
        extra_issues.add("no_confirmed_participants")
    return participants, occupied, positions, extra_issues


def _finalize(hand: _Hand, session_id: str, index: int) -> dict:
    participants, occupied, positions, extra_issues = _assign_positions(hand)
    seats_out = []
    for seat in sorted(set(occupied) | set(participants)):
        sd = hand.seat_data[seat]
        seat_issues = ["initial_stack_may_include_posting"] if sd["stack_first"] is not None else []
        username = None
        if sd["usernames"]:
            username = sd["usernames"].most_common(1)[0][0]
            if len(sd["usernames"]) > 1:
                seat_issues.append("username_ambiguous")
        entry = {
            "seat": seat,
            "username": username,
            "username_source": ('user' if sd['username_sources'].get(username)=={'user'} else
                                'mixed' if 'user' in sd['username_sources'].get(username,set()) else 'ocr'),
            "position": positions.get(seat),
            "position_status": "provisional" if seat in positions else "unknown",
            "stack_bb": sd["stack_first"],
            "participant": True if seat in participants else None,
            "issues": seat_issues,
        }
        parts=[{feature:next(iter(values)) if len(values)==1 else None
                for feature,values in card.items()} for card in sd['hole_readings']]
        if any(len(values)>1 for card in sd['hole_readings'] for values in card.values()):
            seat_issues.append('hole_card_readings_conflict')
        entry['hole_card_parts']=parts
        entry['hole_cards']=[c['rank']+c['suit'] for c in parts] if all(c['rank'] and c['suit'] for c in parts) else None
        if entry['hole_cards'] and len(set(entry['hole_cards']))!=2:
            entry['hole_cards']=None
            seat_issues.append('duplicate_hole_card_readings')
        entry['hole_card_evidence']=sd['hole_card_evidence']
        if len(sd["usernames"]) > 1:
            entry["username_candidates"] = sorted(sd["usernames"])
        seats_out.append(entry)

    issues = set(hand.issues) | extra_issues
    if not hand.start_known:
        issues.add("start_unknown")
    if not hand.end_known:
        issues.add("end_unknown")
    issues.update({"action_sequence_may_be_incomplete", "chip_accounting_unverified"})
    if any(a["time"] == b["time"] for a,b in zip(hand.events,hand.events[1:])):
        issues.add("simultaneous_observations_action_order_unknown")

    return {
        "id": _make_id(session_id, index, hand.start_time),
        "session_id": session_id,
        "start_time": hand.start_time,
        "end_time": hand.last_obs_time,
        "start_known": hand.start_known,
        "end_known": hand.end_known,
        "boundary_reason": hand.boundary_reason,
        "dealer_seat": hand.dealer_seat,
        "seats": seats_out,
        "events": hand.events,
        "board": None if 'board_readings_conflict' in issues else hand.board,
        "hero_cards": hand.hero_cards,
        "pot_observations": hand.pot_observations,
        "evidence": hand.evidence,
        "needs_review": True,
        "issues": sorted(issues),
    }


def reconstruct(session_id: str, observations: list[dict]) -> list[dict]:
    """Reconstruct a bounded, conservative list of hands from observations.

    Never invents actions, winners, or hole cards. Dealer-seat changes need
    two consecutive confirming observations before they count as a hand
    boundary; a single differing reading that reverts is treated as OCR
    noise. Table becoming not-visible always ends the current hand as
    incomplete (end_known=False) rather than guessing what happened.
    """
    if not observations:
        return []
    obs = sorted(observations, key=lambda o: o["time"])

    hands: list[dict] = []
    current: _Hand | None = None
    confirmed_dealer = None
    buffer: list[dict] = []
    pending_dealer = None

    def close_current(end_known, reason=None):
        nonlocal current
        if current is None:
            return
        current.end_known = end_known
        if reason:
            current.issues.add(reason)
        hands.append(_finalize(current, session_id, len(hands)))
        current = None

    for o in obs:
        if not o.get("table_visible"):
            close_current(False, "table_not_visible")
            confirmed_dealer = None
            buffer = []
            pending_dealer = None
            continue

        dealer = o.get("dealer_seat")

        if current is None:
            current = _Hand(
                o["time"],
                start_known=not hands,
                boundary_reason="capture_start" if not hands else "resume_after_gap",
                dealer_seat=dealer,
            )
            if not hands:
                # start_known is False for the very first observed hand: we
                # cannot know whether capture began exactly at a hand start.
                current.start_known = False
            confirmed_dealer = dealer
            buffer = []
            pending_dealer = None
            current.accumulate(o)
            continue

        if dealer is None or confirmed_dealer is None or dealer == confirmed_dealer:
            if buffer:
                for b in buffer:
                    current.accumulate(b)
                buffer = []
                pending_dealer = None
            if confirmed_dealer is None and dealer is not None:
                confirmed_dealer = dealer
                current.dealer_seat = dealer
            current.accumulate(o)
            continue

        # dealer differs from the confirmed value: needs two consecutive
        # confirmations before it counts as a real boundary.
        if dealer == pending_dealer:
            buffer.append(o)
        else:
            for b in buffer:
                current.accumulate(b)
            buffer = [o]
            pending_dealer = dealer

        if len(buffer) >= 2:
            close_current(True, "dealer_change")
            current = _Hand(buffer[0]["time"], start_known=True,
                             boundary_reason="dealer_change", dealer_seat=dealer)
            confirmed_dealer = dealer
            for b in buffer:
                current.accumulate(b)
            buffer = []
            pending_dealer = None

    if current is not None:
        for b in buffer:
            current.accumulate(b)
        close_current(False, "observations_ended")

    return hands
