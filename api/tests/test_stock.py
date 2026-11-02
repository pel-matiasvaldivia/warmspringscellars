"""The planner on its own: who a release can fill, given the bottles there are.

No database and no HTTP here. The arithmetic is the part that decides whether
a club charges two hundred people for a hundred and eighty shipments, so it is
worth being able to read the cases without a service around them.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.stock import (  # noqa: E402
    NO_COMPOSITION, SHORT, UNKNOWN_TIER, UNSHIPPABLE,
    Line, Member, Wine, plan_release,
)

CAB, CHARD, ZIN = 1, 2, 3

WINES = {
    CAB: Wine(CAB, "EST-CAB-21", "2021 Estate Cabernet"),
    CHARD: Wine(CHARD, "EST-CHD-23", "2023 Estate Chardonnay"),
    ZIN: Wine(ZIN, "OLD-ZIN-20", "2020 Old Vine Zinfandel"),
}

TIERS = {"founders-reserve": ("The Founder's Reserve", 6), "full-case": ("The Full Case", 12)}

SHIPPABLE = frozenset({"California", "Oregon", "New York"})

# Six bottles: three Cabernet, two Chardonnay, one Zinfandel.
SIX = [
    Line("founders-reserve", CAB, 3),
    Line("founders-reserve", CHARD, 2),
    Line("founders-reserve", ZIN, 1),
]
# Twelve: double it.
TWELVE = [
    Line("full-case", CAB, 6),
    Line("full-case", CHARD, 4),
    Line("full-case", ZIN, 2),
]


def member(member_id: int, tier: str = "founders-reserve", *, state: str = "California",
           card: bool = True) -> Member:
    return Member(id=member_id, tier_key=tier, name=f"Member {member_id}",
                  state=state, has_card=card)


def run(members, lines, on_hand, tiers=None):
    return plan_release(members=members, lines=lines, on_hand=on_hand, wines=WINES,
                        tiers=tiers if tiers is not None else TIERS,
                        shippable_states=SHIPPABLE)


# ── the ordinary case ────────────────────────────────────────────────────
def test_enough_wine_fills_everyone():
    plan = run([member(1), member(2), member(3)], SIX, {CAB: 9, CHARD: 6, ZIN: 3})

    assert plan.complete
    assert plan.covered == [1, 2, 3]
    assert plan.bottles == 18
    assert plan.allocation == {CAB: 9, CHARD: 6, ZIN: 3}
    assert [w.left_over for w in plan.wines] == [0, 0, 0]


def test_two_tiers_draw_on_the_same_bottles():
    plan = run([member(1, "founders-reserve"), member(2, "full-case")],
               SIX + TWELVE, {CAB: 9, CHARD: 6, ZIN: 3})

    assert plan.complete
    assert plan.allocation == {CAB: 9, CHARD: 6, ZIN: 3}
    cab = next(w for w in plan.wines if w.wine_id == CAB)
    assert cab.per_tier == {"founders-reserve": 3, "full-case": 6}
    assert cab.demanded == 9


def test_what_is_left_over_is_reported():
    plan = run([member(1)], SIX, {CAB: 10, CHARD: 10, ZIN: 10})

    left = {w.sku: w.left_over for w in plan.wines}
    assert left == {"EST-CAB-21": 7, "EST-CHD-23": 8, "OLD-ZIN-20": 9}


# ── running out ──────────────────────────────────────────────────────────
def test_the_wine_running_out_leaves_the_last_member_unfilled():
    plan = run([member(1), member(2), member(3)], SIX, {CAB: 9, CHARD: 4, ZIN: 9})

    assert not plan.complete
    assert plan.covered == [1, 2]
    assert [u.membership_id for u in plan.unfilled] == [3]
    assert plan.unfilled[0].reason == SHORT
    assert "Chardonnay" in plan.unfilled[0].detail


def test_the_members_who_joined_first_keep_their_allocation():
    """The policy, and the only reason the planner walks a list at all."""
    plan = run([member(n) for n in (11, 12, 13, 14, 15)], SIX,
               {CAB: 9, CHARD: 9, ZIN: 9})

    assert plan.covered == [11, 12, 13]
    assert [u.membership_id for u in plan.unfilled] == [14, 15]


def test_seniority_is_per_bottle_not_one_queue():
    """A later member in a tier whose wine held out is still filled.

    Holding their shipment back would not put a bottle in the hands of the
    member who missed out — they are drinking different wine.
    """
    only_zin = [Line("full-case", ZIN, 12)]
    plan = run([member(1, "founders-reserve"), member(2, "founders-reserve"),
                member(3, "full-case")],
               SIX + only_zin, {CAB: 3, CHARD: 2, ZIN: 13})

    assert plan.covered == [1, 3]
    assert [u.membership_id for u in plan.unfilled] == [2]


def test_how_short_each_wine_is_is_reported_against_full_demand():
    plan = run([member(n) for n in (1, 2, 3, 4)], SIX, {CAB: 12, CHARD: 3, ZIN: 12})

    short = {w.sku: w.short_by for w in plan.wines}
    assert short == {"EST-CAB-21": 0, "EST-CHD-23": 5, "OLD-ZIN-20": 0}
    assert [w.sku for w in plan.short_of] == ["EST-CHD-23"]


def test_no_stock_at_all_fills_nobody_and_blames_the_wine():
    plan = run([member(1)], SIX, {})

    assert plan.covered == []
    assert plan.unfilled[0].reason == SHORT
    assert plan.bottles == 0


# ── things that are not a stock problem ──────────────────────────────────
def test_a_tier_this_release_does_not_compose_is_named_as_such():
    plan = run([member(1, "founders-reserve"), member(2, "full-case")],
               SIX, {CAB: 99, CHARD: 99, ZIN: 99})

    assert plan.covered == [1]
    assert plan.unfilled[0].reason == NO_COMPOSITION
    assert plan.count(NO_COMPOSITION) == 1


def test_a_state_we_can_no_longer_ship_to_is_not_allocated_wine():
    """The licensed list can shrink after somebody joined.

    Allocating to them would hold bottles for a shipment no carrier will move,
    so they are left out — but with a reason that is plainly not "we ran out".
    """
    plan = run([member(1, state="Utah"), member(2)], SIX, {CAB: 3, CHARD: 2, ZIN: 1})

    assert plan.covered == [2]
    assert plan.unfilled[0].reason == UNSHIPPABLE
    assert "Utah" in plan.unfilled[0].detail


def test_a_tier_that_left_the_catalogue_is_named_rather_than_guessed():
    plan = run([member(1, "cellar-door")], SIX, {CAB: 99, CHARD: 99, ZIN: 99})

    assert plan.covered == []
    assert plan.unfilled[0].reason == UNKNOWN_TIER


def test_a_member_with_no_card_is_still_allocated_but_flagged():
    plan = run([member(1, card=False)], SIX, {CAB: 3, CHARD: 2, ZIN: 1})

    assert plan.covered == [1]
    assert plan.filled[0].warning == "no card on file"


# ── the composition itself ───────────────────────────────────────────────
def test_a_composition_that_is_not_the_promised_bottle_count_is_caught():
    five = [Line("founders-reserve", CAB, 3), Line("founders-reserve", CHARD, 2)]
    plan = run([member(1)], five, {CAB: 9, CHARD: 9})

    founders = next(t for t in plan.tiers if t.tier_key == "founders-reserve")
    assert founders.composed == 5
    assert founders.bottles == 6
    assert not founders.balanced
    assert [t.tier_key for t in plan.unbalanced] == ["founders-reserve"]


def test_an_unbalanced_tier_with_no_members_is_not_complained_about():
    """Nobody is owed it, so it is not yet a promise anyone can break."""
    five = [Line("full-case", CAB, 5)]
    plan = run([member(1)], SIX + five, {CAB: 99, CHARD: 99, ZIN: 99})

    assert plan.unbalanced == []


def test_a_zero_bottle_line_is_not_a_wine_in_the_shipment():
    plan = run([member(1)], SIX + [Line("founders-reserve", ZIN, 0)],
               {CAB: 3, CHARD: 2, ZIN: 1})

    founders = next(t for t in plan.tiers if t.tier_key == "founders-reserve")
    assert founders.composed == 6


def test_an_uncomposed_release_says_so_rather_than_looking_like_a_shortage():
    """Bottles in the cellar are not a composition.

    This flag is what decides whether a release gates its orders on stock, so
    a cellar with wine in it and nothing said about what goes in the box must
    read as uncomposed — otherwise stocking the cellar would silently stop a
    release raising anything at all.
    """
    plan = run([member(1)], [], {CAB: 99})

    assert not plan.composed
    assert plan.unfilled[0].reason == NO_COMPOSITION


# ── the ceiling ──────────────────────────────────────────────────────────
def test_the_ceiling_is_the_scarcest_wine_in_the_tier():
    plan = run([], SIX, {CAB: 30, CHARD: 6, ZIN: 30})

    founders = next(t for t in plan.tiers if t.tier_key == "founders-reserve")
    assert founders.ceiling == 3           # six Chardonnay, two per shipment
    full = next(t for t in plan.tiers if t.tier_key == "full-case")
    assert full.ceiling is None            # not composed, so there is no number


def test_no_members_is_a_plan_that_fills_nobody_and_takes_nothing():
    plan = run([], SIX, {CAB: 30, CHARD: 30, ZIN: 30})

    assert plan.complete
    assert plan.covered == []
    assert plan.allocation == {}
