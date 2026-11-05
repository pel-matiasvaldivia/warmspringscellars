"""The UPS Ground rate table, read against the sheet it was copied from.

Every figure asserted here was read off Crown's 2026 UPS Ground sheet by hand.
That is the point: a transcription is only worth what its proofreading is, so
the corners of the table — first column, last column, each zone, both service
levels — are each pinned to the paper.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.shipping import (  # noqa: E402
    COLUMNS, RATES, ZONES, Estimate, NoRate, billed, dearest, estimate,
    ice_pack_cents, quote,
    zone_for,
)


# ── the four corners of the sheet ────────────────────────────────────────
def test_the_cheapest_box_on_the_sheet():
    """One bottle, business, inside California: the lowest price printed."""
    q = quote("California", 1, residential=False)
    assert q.carriage_cents == 2800
    assert q.zone == "West Coast"
    assert q.column == "1 bottle"


def test_the_dearest_box_on_the_sheet():
    """Fifteen bottles, residential, East Coast."""
    q = quote("New York", 15)
    assert q.carriage_cents == 8900
    assert q.column == "15 bottles"


def test_the_two_shipments_the_club_actually_sends():
    """The Founder's Reserve and The Full Case, to a house."""
    assert quote("New York", 6).carriage_cents == 5500
    assert quote("New York", 12).carriage_cents == 7900
    assert quote("California", 6).carriage_cents == 4600
    assert quote("California", 12).carriage_cents == 5700


def test_each_zone_prices_a_six_pack_differently():
    six = {z: quote(s, 6).carriage_cents for z, s in (
        ("East and Midwest", "New York"),
        ("Plains", "Missouri"),
        ("Mountain and Northwest", "Colorado"),
        ("West Coast", "Oregon"),
    )}
    assert six == {
        "East and Midwest": 5500,
        "Plains": 5400,
        "Mountain and Northwest": 5100,
        "West Coast": 4600,
    }


def test_residential_costs_more_than_business_everywhere():
    for zone, codes in ZONES.items():
        business = RATES[(zone, False)]
        residential = RATES[(zone, True)]
        assert all(r > b for b, r in zip(business, residential)), zone


def test_a_club_shipment_is_residential_unless_told_otherwise():
    assert quote("New York", 6).residential is True
    assert quote("New York", 6).carriage_cents > \
        quote("New York", 6, residential=False).carriage_cents


# ── the columns ──────────────────────────────────────────────────────────
def test_five_and_six_bottles_share_one_column():
    assert quote("Texas", 5).carriage_cents == quote("Texas", 6).carriage_cents
    assert quote("Texas", 5).column == "5-6 bottles"


def test_thirteen_and_fourteen_bottles_fall_into_the_fifteen_column():
    """The sheet prices nothing between 12 and 15, so they pay the 15 rate."""
    fifteen = quote("Texas", 15).carriage_cents
    assert quote("Texas", 13).carriage_cents == fifteen
    assert quote("Texas", 14).carriage_cents == fifteen


def test_every_zone_has_a_price_for_every_column():
    for zone in ZONES:
        for residential in (True, False):
            assert len(RATES[(zone, residential)]) == len(COLUMNS)


def test_more_bottles_never_costs_less():
    for zone in ZONES:
        for residential in (True, False):
            row = RATES[(zone, residential)]
            assert list(row) == sorted(row), (zone, residential)


# ── what the sheet will not answer ───────────────────────────────────────
@pytest.mark.parametrize("state", ["Utah", "Alaska", "Hawaii"])
def test_the_three_states_the_ground_sheet_does_not_cover(state):
    with pytest.raises(NoRate) as refused:
        quote(state, 6)
    assert state in str(refused.value)


def test_a_shipment_too_big_for_the_sheet_says_so_rather_than_guessing():
    with pytest.raises(NoRate) as refused:
        quote("California", 16)
    assert "two boxes" in str(refused.value)


def test_something_that_is_not_a_state_is_refused():
    with pytest.raises(NoRate):
        quote("Ontario", 6)


def test_an_empty_shipment_has_no_price():
    with pytest.raises(NoRate):
        quote("California", 0)


def test_every_state_we_can_ship_to_either_prices_or_says_why():
    """No state falls through the table into a crash."""
    from app.config import US_STATES

    for state in US_STATES:
        try:
            assert quote(state, 6).carriage_cents > 0
        except NoRate as exc:
            assert state in str(exc)


# ── ice packs ────────────────────────────────────────────────────────────
def test_the_ice_pack_follows_the_rate_sheets_not_the_guide():
    """Crown's two documents disagree at exactly six bottles.

    All four rate sheets say $5 up to four bottles and $7 from five; the
    shipping guide says $5 up to six. The sheets win, and the dearer reading
    is the safer one to plan against.
    """
    assert ice_pack_cents(4) == 500
    assert ice_pack_cents(5) == 700
    assert ice_pack_cents(6) == 700
    assert ice_pack_cents(12) == 700


def test_an_ice_pack_is_added_to_the_carriage_not_folded_into_it():
    plain = quote("New York", 6)
    iced = quote("New York", 6, ice_pack=True)

    assert plain.ice_cents == 0
    assert iced.carriage_cents == plain.carriage_cents
    assert iced.ice_cents == 700
    assert iced.total_cents == 6200


# ── costing a whole release ──────────────────────────────────────────────
# wine, and the carriage the member is billed for it
FOUNDERS, FULL_CASE = 27500, 55000
TASTING, TASTING_SHIP = 12000, 4000


def test_a_release_is_costed_by_zone_and_in_total():
    out = estimate([
        ("Ada", "California", 6, FOUNDERS, 0),
        ("Grace", "Oregon", 12, FULL_CASE, 0),
        ("Alan", "New York", 6, FOUNDERS, 0),
    ])

    assert out.shipments == 3
    assert out.carriage_cents == 4600 + 5700 + 5500
    assert out.ice_cents == 2100
    assert out.with_ice_cents == 15800 + 2100
    assert out.revenue_cents == FOUNDERS * 2 + FULL_CASE

    west = next(z for z in out.zones if z.zone == "West Coast")
    assert west.shipments == 2
    assert west.bottles == 18
    assert west.carriage_cents == 4600 + 5700


def test_the_share_of_revenue_is_what_the_desk_is_looking_for():
    out = estimate([("Alan", "New York", 6, FOUNDERS, 0)])

    assert round(out.share(), 1) == 20.0            # $55 of $275
    assert round(out.share(with_ice=True), 1) == 22.5  # $62 of $275


def test_a_member_the_sheet_cannot_price_is_listed_not_counted_as_free():
    out = estimate([
        ("Ada", "California", 6, FOUNDERS, 0),
        ("Mallory", "Utah", 6, FOUNDERS, 0),
    ])

    assert out.shipments == 1
    assert out.carriage_cents == 4600
    assert [name for name, _ in out.unpriced] == ["Mallory"]
    assert "Utah" in out.unpriced[0][1]
    # their money still counts, so the share is not flattered
    assert out.revenue_cents == FOUNDERS * 2


def test_costing_nothing_is_zero_and_not_a_division_by_zero():
    out = estimate([])
    assert out == Estimate()
    assert out.share() == 0.0
    assert out.shipments == 0


def test_the_zones_come_back_in_sheet_order():
    out = estimate([
        ("Ada", "California", 6, FOUNDERS, 0),
        ("Alan", "New York", 6, FOUNDERS, 0),
        ("Grace", "Colorado", 6, FOUNDERS, 0),
    ])
    assert [z.zone for z in out.zones] == [
        "East and Midwest", "Mountain and Northwest", "West Coast"]


def test_every_tier_the_club_sells_has_a_price_on_the_sheet():
    """A tier the rate sheet cannot cost is a tier nobody can budget for.

    The sheet prices 1 to 12 bottles and then jumps to 15. A tier invented
    outside that range would reach the plan screen and have no freight, so
    the catalogue is walked here rather than trusted.
    """
    from app.catalogue import catalogue

    for tier in catalogue().tiers:
        for state in ("New York", "California"):
            assert quote(state, tier.bottles).carriage_cents > 0, tier.key


def test_the_three_bottle_case_is_the_worst_box_to_send():
    """Freight does not fall as fast as the bottle count.

    Three bottles to the East Coast costs $40 against $55 for six and $79 for
    twelve — so per bottle it is $13.33, $9.17 and $6.58. This is the number
    behind the note in tiers.json saying the three-bottle price is not signed
    off.
    """
    per_bottle = {
        n: quote("New York", n).carriage_cents / n for n in (3, 6, 12)
    }
    assert round(per_bottle[3]) == 1333
    assert round(per_bottle[6]) == 917
    assert round(per_bottle[12]) == 658
    assert per_bottle[3] > per_bottle[6] > per_bottle[12]


def test_a_three_bottle_box_takes_the_small_ice_pack():
    """Three is inside the 1-to-4 band, so $5 rather than $7."""
    assert quote("New York", 3, ice_pack=True).ice_cents == 500
    assert quote("New York", 6, ice_pack=True).ice_cents == 700


def test_zone_for_names_the_zone_a_state_sits_in():
    assert zone_for("Mississippi") == "East and Midwest"
    assert zone_for("Wyoming") == "Plains"
    assert zone_for("Washington") == "Mountain and Northwest"
    assert zone_for("Nevada") == "West Coast"


def test_what_the_winery_absorbs_is_the_gap_between_cost_and_what_is_billed():
    """The Tasting Case bills $40; Crown charges $40 plus $5 of ice to the East Coast.

    So the member covers the carriage and the winery still carries the ice
    pack. That gap is the number the desk is looking at.
    """
    out = estimate([
        ("Ada", "New York", 3, TASTING, TASTING_SHIP),
        ("Grace", "New York", 6, FOUNDERS, 0),
    ])

    assert out.carriage_cents == 4000 + 5500
    assert out.ice_cents == 500 + 700
    assert out.recovered_cents == 4000
    assert out.absorbed_cents == 4000 + 5500 + 500 + 700 - 4000


def test_a_release_of_tiers_that_include_shipping_recovers_nothing():
    out = estimate([("Ada", "California", 12, FULL_CASE, 0)])

    assert out.recovered_cents == 0
    assert out.absorbed_cents == out.with_ice_cents


# ── what the member is billed, once the tier's credit comes off ──────────
def test_the_credit_comes_off_the_carriage_for_that_destination():
    """The Founder's Reserve puts $20 towards whatever the box costs."""
    assert billed("New York", 6, 2000) == 5500 - 2000
    assert billed("California", 6, 2000) == 4600 - 2000
    # the winery gives both members the same $20; they pay different totals
    assert billed("New York", 6, 2000) - billed("California", 6, 2000) == 900


def test_no_credit_means_the_member_pays_the_sheet():
    assert billed("New York", 3, 0) == 4000
    assert billed("California", 3, 0) == 3600


def test_a_credit_larger_than_the_carriage_ships_free_and_refunds_nothing():
    assert billed("California", 3, 3600) == 0
    assert billed("California", 3, 99999) == 0


def test_a_destination_the_sheet_cannot_price_falls_back_to_the_dearest_zone():
    """Alaska has no ground price, so the winery does not guess downwards."""
    assert dearest(6) == 5500
    assert billed("Alaska", 6, 0) == 5500
    assert billed("Alaska", 6, 2000) == 3500
    # and something that is not a state at all behaves the same way
    assert billed("", 6, 0) == 5500


def test_the_credits_the_club_actually_offers():
    """Glen Ellen is the usual case, so these are the figures the desk sees."""
    from app.catalogue import catalogue

    paid = {t.key: billed("California", t.bottles, t.shipping_credit_cents)
            for t in catalogue().tiers}
    assert paid == {"tasting-case": 3600, "founders-reserve": 2600, "full-case": 1700}
