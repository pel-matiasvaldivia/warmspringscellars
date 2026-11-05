"""What Crown charges to send a shipment, from the 2026 UPS Ground rate sheet.

The club promises "shipping included", which is a sentence about the price the
member pays and says nothing about what the carriage costs. Until now nothing
here knew that number, so a release could be composed, raised and charged
without anybody seeing that a six-bottle box to the East Coast takes a fifth of
its own price in freight.

This module is the rate sheet, transcribed, and one function that reads it. No
database, no network: the numbers are a table and the lookup is arithmetic, so
both can be checked against the paper they came from.

ONE CARRIER AND ONE SERVICE. UPS Ground, which is what a club shipment to a
house actually is and the cheaper of the two ground sheets almost everywhere.
FedEx and the air services are not here; add them when somebody decides they
are used, not before.

WHAT THIS DOES NOT COMPUTE. Crown charges $1 a pound over the base weight of
each column, and says an ice pack may itself push a box overweight. Whether it
does is their scale's call on the day, so the figures here are a floor, not a
quotation. Neither are the fees that only happen when something goes wrong —
an address correction at $23, a returned package charged the carriage twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Crown's columns, and the bottle count each one covers. The sheet prices 1, 2,
# 3 and 4 bottles separately, puts 5 and 6 together, then prices 7 through 12
# singly, and ends at a 15-bottle column with nothing between 12 and 15.
COLUMNS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "1 bottle"),
    (2, 2, "2 bottles"),
    (3, 3, "3 bottles"),
    (4, 4, "4 bottles"),
    (5, 6, "5-6 bottles"),
    (7, 7, "7 bottles"),
    (8, 8, "8 bottles"),
    (9, 9, "9 bottles"),
    (10, 10, "10 bottles"),
    (11, 11, "11 bottles"),
    (12, 12, "12 bottles"),
    (13, 15, "15 bottles"),
)
MAX_BOTTLES = COLUMNS[-1][1]

# The zones as the sheet groups them, by postal code.
ZONES: dict[str, frozenset[str]] = {
    "East and Midwest": frozenset({
        "AL", "CT", "DC", "DE", "FL", "GA", "IL", "IN", "KS", "KY", "LA", "MA",
        "MD", "ME", "MI", "MN", "MS", "NC", "ND", "NH", "NJ", "NY", "OH", "OK",
        "PA", "RI", "SC", "TN", "TX", "VA", "VT", "WI", "WV",
    }),
    "Plains": frozenset({"AR", "IA", "MO", "MT", "NE", "SD", "WY"}),
    "Mountain and Northwest": frozenset({"AZ", "CO", "ID", "NM", "WA"}),
    "West Coast": frozenset({"CA", "NV", "OR"}),
}

# Dollars, in the column order above. Transcribed from the 2026 UPS Ground
# sheet; the residential row already carries Crown's residential surcharge.
RATES: dict[tuple[str, bool], tuple[int, ...]] = {
    ("East and Midwest", False): (30, 33, 35, 40, 48, 55, 59, 63, 66, 69, 72, 82),
    ("East and Midwest", True): (35, 38, 40, 45, 55, 62, 66, 70, 73, 76, 79, 89),
    ("Plains", False): (29, 32, 34, 39, 47, 53, 57, 61, 63, 66, 69, 79),
    ("Plains", True): (34, 37, 39, 44, 54, 60, 64, 68, 70, 73, 76, 86),
    ("Mountain and Northwest", False): (29, 31, 33, 37, 44, 51, 52, 53, 54, 55, 56, 66),
    ("Mountain and Northwest", True): (34, 36, 38, 42, 51, 58, 59, 60, 61, 62, 63, 73),
    ("West Coast", False): (28, 30, 31, 34, 39, 45, 46, 47, 48, 49, 50, 60),
    ("West Coast", True): (33, 35, 36, 39, 46, 52, 53, 54, 55, 56, 57, 67),
}

# Crown's own two documents disagree here. The shipping guide says $5 for 1 to
# 6 bottles and $7 for 7 to 12; all four rate sheets say $5 for 1 to 4 and $7
# for 5 to 12. Six bottles — The Founder's Reserve — is the one count where it
# matters. The sheets win because all four agree and only the guide differs,
# and because the dearer reading is the safer one to plan against. Ask Crown.
ICE_PACK_SMALL_MAX = 4
ICE_PACK_SMALL = 500
ICE_PACK_LARGE = 700

# Crown's ground sheet prices no zone containing these, so a shipment there is
# not something this table can answer.
NOT_SERVED = frozenset({"AK", "HI", "UT"})

STATE_CODES: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN",
    "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}


class NoRate(LookupError):
    """The sheet has no price for this. Carries a sentence for the desk."""


@dataclass(frozen=True)
class Quote:
    zone: str
    column: str
    bottles: int
    residential: bool
    carriage_cents: int
    ice_cents: int

    @property
    def total_cents(self) -> int:
        return self.carriage_cents + self.ice_cents


def zone_for(state: str) -> str:
    """Which of Crown's four zones a state sits in."""
    code = STATE_CODES.get(state.strip())
    if code is None:
        raise NoRate(f"{state} is not a state the rate sheet names.")
    if code in NOT_SERVED:
        raise NoRate(f"Crown's ground sheet prices no zone covering {state}.")
    for name, codes in ZONES.items():
        if code in codes:
            return name
    raise NoRate(f"{state} is in none of Crown's ground zones.")


def _column(bottles: int) -> int:
    for index, (low, high, _) in enumerate(COLUMNS):
        if low <= bottles <= high:
            return index
    raise NoRate(
        f"The sheet stops at {MAX_BOTTLES} bottles, and this is {bottles}. "
        "A larger shipment is two boxes, or a price from Crown."
    )


def ice_pack_cents(bottles: int) -> int:
    return ICE_PACK_SMALL if bottles <= ICE_PACK_SMALL_MAX else ICE_PACK_LARGE


def quote(state: str, bottles: int, *, residential: bool = True,
          ice_pack: bool = False) -> Quote:
    """What Crown charges to send this many bottles to this state.

    Residential by default: a club shipment goes to somebody's house, and the
    business rate would quietly understate every estimate the desk reads.
    """
    if bottles < 1:
        raise NoRate("A shipment with no bottles in it has no price.")
    zone = zone_for(state)
    index = _column(bottles)
    return Quote(
        zone=zone,
        column=COLUMNS[index][2],
        bottles=bottles,
        residential=residential,
        carriage_cents=RATES[(zone, residential)][index] * 100,
        ice_cents=ice_pack_cents(bottles) if ice_pack else 0,
    )


# ── what a whole release costs to send ───────────────────────────────────
@dataclass
class ZoneCost:
    zone: str
    shipments: int = 0
    bottles: int = 0
    carriage_cents: int = 0
    ice_cents: int = 0

    @property
    def with_ice_cents(self) -> int:
        return self.carriage_cents + self.ice_cents


@dataclass
class Estimate:
    """What a release costs to put in the carrier's hands."""
    zones: list[ZoneCost] = field(default_factory=list)
    unpriced: list[tuple[str, str]] = field(default_factory=list)  # (name, why)
    carriage_cents: int = 0
    ice_cents: int = 0
    revenue_cents: int = 0

    @property
    def shipments(self) -> int:
        return sum(z.shipments for z in self.zones)

    @property
    def with_ice_cents(self) -> int:
        return self.carriage_cents + self.ice_cents

    def share(self, with_ice: bool = False) -> float:
        """Carriage as a percentage of what the release charges."""
        if not self.revenue_cents:
            return 0.0
        spent = self.with_ice_cents if with_ice else self.carriage_cents
        return spent * 100 / self.revenue_cents


def estimate(shipments: list[tuple[str, str, int, int]]) -> Estimate:
    """Cost a release, from (member name, state, bottles, price in cents).

    A member whose state the sheet cannot price is not silently dropped and
    not counted as free: they are listed with the reason, because a release
    that cannot be costed in full is a release the desk should look at before
    it charges anybody.
    """
    out = Estimate()
    by_zone: dict[str, ZoneCost] = {}
    for name, state, bottles, price_cents in shipments:
        out.revenue_cents += price_cents
        try:
            priced = quote(state, bottles, residential=True, ice_pack=True)
        except NoRate as exc:
            out.unpriced.append((name, str(exc)))
            continue
        row = by_zone.setdefault(priced.zone, ZoneCost(priced.zone))
        row.shipments += 1
        row.bottles += bottles
        row.carriage_cents += priced.carriage_cents
        row.ice_cents += priced.ice_cents
        out.carriage_cents += priced.carriage_cents
        out.ice_cents += priced.ice_cents
    out.zones = [by_zone[z] for z in ZONES if z in by_zone]
    return out
