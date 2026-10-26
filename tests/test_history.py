import math
import unittest

from ada_poker.history import reconstruct


def seat(seat, username=None, stack_bb=None, bet_bb=None, action=None,
         has_cards=None, payout_bb=None):
    return {"seat": seat, "username": username, "stack_bb": stack_bb,
            "bet_bb": bet_bb, "action": action, "has_cards": has_cards,
            "payout_bb": payout_bb}


def obs(time, table_visible=True, dealer_seat=None, pot_bb=None, board=None,
        hero_cards=None, seats=None, issues=None, evidence=None):
    return {
        "time": time,
        "evidence": evidence or {"part": "part0", "offset": time, "image": None},
        "table_visible": table_visible,
        "dealer_seat": dealer_seat,
        "pot_bb": pot_bb,
        "board": board,
        "hero_cards": hero_cards,
        "seats": seats or [],
        "issues": issues or [],
    }


class TestEmptyAndMissingTable(unittest.TestCase):
    def test_no_observations_returns_no_hands(self):
        self.assertEqual(reconstruct("s1", []), [])

    def test_never_visible_returns_no_hands(self):
        observations = [obs(t, table_visible=False) for t in (0, 0.2, 0.4)]
        self.assertEqual(reconstruct("s1", observations), [])

    def test_table_disappearing_ends_hand_incomplete(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.2, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.4, table_visible=False),
        ]
        hands = reconstruct("s1", observations)
        self.assertEqual(len(hands), 1)
        hand = hands[0]
        self.assertFalse(hand["end_known"])
        self.assertIn("end_unknown", hand["issues"])
        self.assertIn("table_not_visible", hand["issues"])


class TestDealerStability(unittest.TestCase):
    def test_single_flicker_does_not_create_new_hand(self):
        # A single misread dealer seat that reverts is OCR noise, not a
        # real button move: still one hand.
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.2, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.4, dealer_seat=1, seats=[seat(4, "hero", has_cards=True)]),  # flicker
            obs(0.6, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.8, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        self.assertEqual(len(hands), 1)
        self.assertEqual(hands[0]["dealer_seat"], 0)

    def test_two_consistent_readings_confirm_boundary(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.2, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.4, dealer_seat=1, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.6, dealer_seat=1, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.8, dealer_seat=1, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        self.assertEqual(len(hands), 2)
        self.assertEqual(hands[0]["dealer_seat"], 0)
        self.assertEqual(hands[1]["dealer_seat"], 1)
        self.assertEqual(hands[1]["boundary_reason"], "dealer_change")
        self.assertTrue(hands[1]["start_known"])
        self.assertTrue(hands[0]["end_known"])

    def test_unknown_dealer_gap_does_not_create_boundary(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.2, dealer_seat=None, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.4, dealer_seat=None, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.6, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        self.assertEqual(len(hands), 1)


class TestHeadsUpPositions(unittest.TestCase):
    def test_dealer_is_sb_other_is_bb(self):
        observations = [
            obs(0.0, dealer_seat=4, seats=[
                seat(4, "hero", has_cards=True),
                seat(1, "villain", has_cards=True),
            ]),
            obs(0.2, dealer_seat=4, seats=[
                seat(4, "hero", has_cards=True),
                seat(1, "villain", has_cards=True),
            ]),
        ]
        hands = reconstruct("s1", observations)
        self.assertEqual(len(hands), 1)
        by_seat = {s["seat"]: s for s in hands[0]["seats"]}
        self.assertEqual(by_seat[4]["position"], "SB")
        self.assertEqual(by_seat[1]["position"], "BB")


class TestRepeatedActionLabels(unittest.TestCase):
    def test_repeated_label_is_one_event(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(1, "villain", action="fold")]),
            obs(0.2, dealer_seat=0, seats=[seat(1, "villain", action="fold")]),
            obs(0.4, dealer_seat=0, seats=[seat(1, "villain", action="fold")]),
        ]
        hands = reconstruct("s1", observations)
        fold_events = [e for e in hands[0]["events"] if e["action"] == "fold"]
        self.assertEqual(len(fold_events), 1)

    def test_label_reappearing_after_clearing_is_new_event(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(1, "villain", action="check")]),
            obs(0.2, dealer_seat=0, seats=[seat(1, "villain", action=None)]),
            obs(0.4, dealer_seat=0, seats=[seat(1, "villain", action="check")]),
        ]
        hands = reconstruct("s1", observations)
        check_events = [e for e in hands[0]["events"] if e["action"] == "check"]
        self.assertEqual(len(check_events), 2)


class TestMidHandStartEnd(unittest.TestCase):
    def test_first_hand_is_start_unknown(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        self.assertFalse(hands[0]["start_known"])
        self.assertIn("start_unknown", hands[0]["issues"])

    def test_resume_after_gap_is_start_unknown(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.2, table_visible=False),
            obs(0.4, dealer_seat=1, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        self.assertEqual(len(hands), 2)
        self.assertFalse(hands[0]["end_known"])
        self.assertFalse(hands[1]["start_known"])
        self.assertEqual(hands[1]["boundary_reason"], "resume_after_gap")

    def test_trailing_observations_end_unknown(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
            obs(0.2, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        self.assertEqual(len(hands), 1)
        self.assertFalse(hands[-1]["end_known"])
        self.assertEqual(hands[0]["boundary_reason"], "capture_start")
        self.assertIn("observations_ended", hands[-1]["issues"])


class TestNullValues(unittest.TestCase):
    def test_null_stack_and_bet_are_not_zero(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", stack_bb=None, bet_bb=None,
                                                  has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        hero = next(s for s in hands[0]["seats"] if s["seat"] == 4)
        self.assertIsNone(hero["stack_bb"])

    def test_seat_with_no_data_is_not_reported(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        seats_present = {s["seat"] for s in hands[0]["seats"]}
        self.assertNotIn(2, seats_present)


class TestRejectedAmounts(unittest.TestCase):
    def test_nan_and_infinite_and_negative_amounts_rejected(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[
                seat(4, "hero", stack_bb=float("nan"), has_cards=True),
                seat(1, "villain", stack_bb=float("inf"), bet_bb=-5, action="bet"),
            ]),
        ]
        hands = reconstruct("s1", observations)
        by_seat = {s["seat"]: s for s in hands[0]["seats"]}
        self.assertIsNone(by_seat[4]["stack_bb"])
        self.assertIsNone(by_seat[1]["stack_bb"])
        bet_events = [e for e in hands[0]["events"] if e["seat"] == 1]
        self.assertEqual(bet_events[0]["amount_bb"], None)
        self.assertIn("amount_unavailable", bet_events[0]["issues"])


class TestUsernameChanges(unittest.TestCase):
    def test_conflicting_usernames_flagged_not_merged(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(1, "Alice", action="call")]),
            obs(0.2, dealer_seat=0, seats=[seat(1, "AIice", action="call")]),
        ]
        hands = reconstruct("s1", observations)
        villain = next(s for s in hands[0]["seats"] if s["seat"] == 1)
        self.assertIn("username_ambiguous", villain["issues"])
        self.assertIn("Alice", villain["username_candidates"])
        self.assertIn("AIice", villain["username_candidates"])


class TestContributionChange(unittest.TestCase):
    def test_stack_drop_without_action_is_contribution_change_not_raise(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(1, "villain", stack_bb=100)]),
            obs(0.2, dealer_seat=0, seats=[seat(1, "villain", stack_bb=95)]),
        ]
        hands = reconstruct("s1", observations)
        events = [e for e in hands[0]["events"] if e["seat"] == 1]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "stack_change")
        self.assertEqual(events[0]["origin"], "observed")
        self.assertAlmostEqual(events[0]["stack_delta_bb"], -5)
        self.assertIsNone(events[0]["amount_bb"])

    def test_stack_increase_without_action_is_not_a_win(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(1, "villain", stack_bb=90)]),
            obs(0.2, dealer_seat=0, seats=[seat(1, "villain", stack_bb=100)]),
        ]
        hands = reconstruct("s1", observations)
        events = [e for e in hands[0]["events"] if e["seat"] == 1]
        self.assertEqual(events[0]["action"], "stack_change")
        self.assertNotEqual(events[0]["action"], "win")


class TestDeterministicIds(unittest.TestCase):
    def test_ids_are_deterministic_and_include_session(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands1 = reconstruct("session-A", observations)
        hands2 = reconstruct("session-A", observations)
        self.assertEqual(hands1[0]["id"], hands2[0]["id"])
        self.assertTrue(hands1[0]["id"].startswith("session-A:"))


class TestNeedsReview(unittest.TestCase):
    def test_conflicting_hole_cards_stay_unknown(self):
        hand=reconstruct('s',[obs(0,hero_cards=['As','Kh']),obs(.2,hero_cards=['Ad','Kh'])])[0]
        self.assertIsNone(hand['hero_cards'])
        self.assertIn('hero_card_readings_conflict',hand['issues'])

    def test_partial_board_removal_does_not_erase_river(self):
        cards=['8h','8s','7s','9d','5h']
        hand=reconstruct('s',[obs(0,board=cards),obs(.2,board=cards[:3]),obs(.4,board=[],seats=[seat(1,'Player',action='win')])])[0]
        self.assertEqual(hand['board'],cards)
        self.assertEqual(hand['events'][0]['street'],'river')

    def test_later_chip_update_enriches_existing_label(self):
        hand=reconstruct('s',[obs(0,seats=[seat(1,'Player',action='raise')]),
                              obs(.2,seats=[seat(1,'Player',action='raise',bet_bb=2.5)])])[0]
        self.assertEqual(len(hand['events']),1)
        self.assertEqual(hand['events'][0]['to_bb'],2.5)
        self.assertIsNone(hand['events'][0]['amount_bb'])
        self.assertEqual(hand['events'][0]['amount_evidence']['offset'],.2)

    def test_fold_flicker_does_not_create_second_fold(self):
        hand=reconstruct('s',[obs(0,seats=[seat(1,'Player',action='fold')]),
                              obs(.2,seats=[seat(1,'Player')]),
                              obs(.4,seats=[seat(1,'Player',action='fold')])])[0]
        self.assertEqual(len(hand['events']),1)

    def test_call_total_is_not_increment(self):
        hand = reconstruct('s',[obs(0,dealer_seat=0,seats=[seat(1,'Player',bet_bb=3,action='call')])])[0]
        event = hand['events'][0]
        self.assertIsNone(event['amount_bb'])
        self.assertEqual(event['to_bb'],3)

    def test_fold_does_not_inherit_posted_blind(self):
        hand = reconstruct('s',[obs(0,seats=[seat(1,'Player',bet_bb=1,action='fold')])])[0]
        event = hand['events'][0]
        self.assertIsNone(event['amount_bb'])
        self.assertIsNone(event['to_bb'])

    def test_missing_dealer_participant_does_not_assign_someone_else_btn(self):
        hand = reconstruct('s',[obs(0,dealer_seat=0,seats=[seat(1,'Player',has_cards=True),seat(2,'Other',has_cards=True)])])[0]
        self.assertTrue(all(s['position'] is None for s in hand['seats']))

    def test_observation_uncertainty_survives_reconstruction(self):
        hand = reconstruct('s',[obs(0,issues=['ocr_ambiguous'])])[0]
        self.assertIn('ocr_ambiguous',hand['issues'])

    def test_all_hands_marked_needs_review(self):
        observations = [
            obs(0.0, dealer_seat=0, seats=[seat(4, "hero", has_cards=True)]),
        ]
        hands = reconstruct("s1", observations)
        self.assertTrue(hands[0]["needs_review"])


if __name__ == "__main__":
    unittest.main()
