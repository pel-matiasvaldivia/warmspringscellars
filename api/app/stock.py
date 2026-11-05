"""What a release can actually ship, given the bottles there are.

Until now a release raised one order per active member and nobody asked
whether the wine existed. With a few hundred members and a few thousand
bottles that is a club that will one day charge two hundred people for a
hundred and eighty shipments — and the way that goes wrong is the worst way,
because the money is taken before anyone counts.

So a release is composed before it is raised: for each tier, which wines and
how many bottles of each. From that composition, the bottles on hand and the
members who are owed a shipment, this module answers one question —

    who can be filled, and who cannot, and which wine ran out?

The answer is a list of membership ids, and that list is what the release is
allowed to raise orders for. Nothing here touches the database or takes any
money: it is arithmetic over values the caller has already read, which is why
it can be read and tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Why a member cannot be filled. Separated because they are different
# conversations: one is "we ran out", which is the winery's to explain, and
# the rest are "we could not have shipped this anyway", which is a data
# problem somebody at the desk can fix this morning.
SHORT = "short"              # the wine ran out
UNSHIPPABLE = "unshippable"  # the state is not licensed (it may have been once)
NO_COMPOSITION = "no_composition"  # this release does not say what their tier gets
UNKNOWN_TIER = "unknown_tier"      # their tier is no longer in the catalogue


@dataclass(frozen=True)
class Member:
    """A membership as the planner needs it, in the order they joined."""
    id: int
    tier_key: str
    name: str
    state: str
    has_card: bool


@dataclass(frozen=True)
class Wine:
    id: int
    sku: str
    name: str


@dataclass(frozen=True)
class Line:
    """One wine in one tier's shipment for this release."""
    tier_key: str
    wine_id: int
    bottles: int


@dataclass(frozen=True)
class Filled:
    membership_id: int
    name: str
    tier_key: str
    # Where it goes. Carried here because the walk has it in hand and the
    # carriage is priced per state: looking it up again would mean asking the
    # database the same question twice for every member in the release.
    state: str = ""
    # A card that is not on file is not a reason to withhold wine — the member
    # has a letter and a link, and the order waits at `failed` until they use
    # it. But a bottle allocated to a card that will decline is a bottle taken
    # from somebody who would have paid, so the desk is told.
    warning: str = ""


@dataclass(frozen=True)
class Unfilled:
    membership_id: int
    name: str
    tier_key: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class WineNeed:
    """One row of the stock table: what this wine is being asked for."""
    wine_id: int
    sku: str
    name: str
    on_hand: int
    per_tier: dict[str, int]      # bottles of this wine in one shipment of each tier
    demanded: int                 # if every member were filled
    allocated: int                # under this plan
    @property
    def short_by(self) -> int:
        return max(0, self.demanded - self.on_hand)
    @property
    def left_over(self) -> int:
        return self.on_hand - self.allocated


@dataclass(frozen=True)
class TierNeed:
    tier_key: str
    name: str
    bottles: int                  # what the catalogue promises per shipment
    composed: int                 # what the composition adds up to
    members: int
    filled: int
    # Shipments of this tier the stock could build with nothing else drawing
    # on it. None when the release does not compose this tier at all.
    ceiling: int | None = None

    @property
    def balanced(self) -> bool:
        return self.composed == self.bottles


@dataclass
class Plan:
    """What this release can ship, and what it cannot."""
    filled: list[Filled] = field(default_factory=list)
    unfilled: list[Unfilled] = field(default_factory=list)
    wines: list[WineNeed] = field(default_factory=list)
    tiers: list[TierNeed] = field(default_factory=list)
    # Whether this release says what any tier's shipment holds. Its own field
    # and not "are there any wine rows", because a cellar with bottles in it
    # and no composition produces rows — and this is the flag that decides
    # whether a release is allowed to gate its orders on stock at all.
    composed: bool = False

    @property
    def covered(self) -> list[int]:
        """The membership ids this release may raise orders for."""
        return [f.membership_id for f in self.filled]

    @property
    def allocation(self) -> dict[int, int]:
        """Bottles per wine this plan commits. Signed positive."""
        return {w.wine_id: w.allocated for w in self.wines if w.allocated}

    @property
    def bottles(self) -> int:
        return sum(w.allocated for w in self.wines)

    @property
    def complete(self) -> bool:
        """Everyone owed a shipment gets one."""
        return not self.unfilled

    @property
    def short_of(self) -> list[WineNeed]:
        return [w for w in self.wines if w.short_by]

    @property
    def unbalanced(self) -> list[TierNeed]:
        """Tiers whose composition is not the number of bottles promised."""
        return [t for t in self.tiers if t.members and not t.balanced]

    def count(self, reason: str) -> int:
        return sum(1 for u in self.unfilled if u.reason == reason)


def plan_release(
    members: list[Member],
    lines: list[Line],
    on_hand: dict[int, int],
    wines: dict[int, Wine],
    tiers: dict[str, tuple[str, int]],
    shippable_states: frozenset[str] | set[str],
) -> Plan:
    """Work out who this release can fill, in the order they joined.

    `members` is in seniority order and that order is the policy: when a wine
    runs out, the people who joined first keep their allocation. It is the
    rule a winery can say out loud to the member who misses out, which is
    more than can be said for any of the alternatives.

    One consequence worth knowing: seniority is per bottle, not a single
    queue. A member who joined later can still be filled after an earlier one
    was not, if they are in a tier whose wines have not run out. That is not
    a bug — they are drinking different wine, and holding back a shipment that
    could have gone out would not put a bottle in the other member's hands.

    `tiers` maps a tier key to its (name, bottles) from the catalogue, so a
    composition that does not add up to what the club promised is reported
    rather than quietly shipped.
    """
    per_tier: dict[str, dict[int, int]] = {}
    for line in lines:
        if line.bottles > 0:
            per_tier.setdefault(line.tier_key, {})[line.wine_id] = line.bottles

    heads: dict[str, int] = {}
    for member in members:
        heads[member.tier_key] = heads.get(member.tier_key, 0) + 1

    plan = Plan(composed=bool(per_tier))
    remaining = {wine_id: int(count) for wine_id, count in on_hand.items()}
    allocated: dict[int, int] = {}
    filled_per_tier: dict[str, int] = {}

    for member in members:
        if member.state not in shippable_states:
            plan.unfilled.append(Unfilled(
                member.id, member.name, member.tier_key, UNSHIPPABLE,
                f"not licensed to ship to {member.state}"))
            continue
        if member.tier_key not in tiers:
            plan.unfilled.append(Unfilled(
                member.id, member.name, member.tier_key, UNKNOWN_TIER,
                f"{member.tier_key} is no longer in the catalogue"))
            continue
        recipe = per_tier.get(member.tier_key)
        if not recipe:
            plan.unfilled.append(Unfilled(
                member.id, member.name, member.tier_key, NO_COMPOSITION,
                "this release does not say what that tier gets"))
            continue

        missing = [wine_id for wine_id, need in recipe.items()
                   if remaining.get(wine_id, 0) < need]
        if missing:
            names = ", ".join(sorted(
                wines[w].name if w in wines else f"wine {w}" for w in missing))
            plan.unfilled.append(Unfilled(
                member.id, member.name, member.tier_key, SHORT,
                f"out of {names}"))
            continue

        for wine_id, need in recipe.items():
            remaining[wine_id] -= need
            allocated[wine_id] = allocated.get(wine_id, 0) + need
        filled_per_tier[member.tier_key] = filled_per_tier.get(member.tier_key, 0) + 1
        plan.filled.append(Filled(
            member.id, member.name, member.tier_key, member.state,
            "" if member.has_card else "no card on file"))

    demanded: dict[int, int] = {}
    for tier_key, recipe in per_tier.items():
        for wine_id, need in recipe.items():
            demanded[wine_id] = demanded.get(wine_id, 0) + need * heads.get(tier_key, 0)

    involved = set(demanded) | set(allocated) | set(on_hand)
    # By name, which is how the cellar lists them: the desk reads the two
    # tables against each other and a different order in each is a way to
    # misread a row.
    for wine_id in sorted(involved, key=lambda w: (
            wines[w].name if w in wines else "", w)):
        wine = wines.get(wine_id)
        plan.wines.append(WineNeed(
            wine_id=wine_id,
            sku=wine.sku if wine else "",
            name=wine.name if wine else f"wine {wine_id}",
            on_hand=int(on_hand.get(wine_id, 0)),
            per_tier={t: recipe[wine_id] for t, recipe in per_tier.items()
                      if wine_id in recipe},
            demanded=demanded.get(wine_id, 0),
            allocated=allocated.get(wine_id, 0),
        ))

    for tier_key, (name, bottles) in tiers.items():
        recipe = per_tier.get(tier_key, {})
        plan.tiers.append(TierNeed(
            tier_key=tier_key,
            name=name,
            bottles=bottles,
            composed=sum(recipe.values()),
            members=heads.get(tier_key, 0),
            filled=filled_per_tier.get(tier_key, 0),
            ceiling=_ceiling(recipe, on_hand),
        ))
    return plan


def _ceiling(recipe: dict[int, int], on_hand: dict[int, int]) -> int | None:
    """Shipments of one tier the stock could build with nothing else drawing on it.

    Not how many will be built — the tiers share bottles and the membership
    mix is not something the winery chooses — but it is the number to look at
    when deciding what to buy or hold back, so the screen shows it.
    """
    if not recipe:
        return None
    return min(int(on_hand.get(wine_id, 0)) // need for wine_id, need in recipe.items())
