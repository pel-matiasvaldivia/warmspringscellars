"""The club's offer, loaded from tiers.json.

One catalogue feeds the offer page, the order lines and the invoice, so a
member is never shown a price different from the one they are charged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CATALOGUE_PATH = Path(__file__).resolve().parent / "tiers.json"


@dataclass(frozen=True)
class Tier:
    key: str
    name: str
    label: str
    bottles: int
    discount_pct: int
    price_cents: int
    shipping_cents: int
    invite_only: bool
    featured: bool
    summary: str
    benefits: tuple[str, ...]

    @property
    def price(self) -> str:
        return f"${self.price_cents / 100:,.2f}"

    @property
    def price_per_bottle_cents(self) -> int:
        return round(self.price_cents / self.bottles)

    @property
    def total_cents(self) -> int:
        """What a shipment actually costs the member, before tax."""
        return self.price_cents + self.shipping_cents


@dataclass(frozen=True)
class Catalogue:
    currency: str
    shipments_per_year: int
    shipment_months: tuple[int, ...]
    tiers: tuple[Tier, ...]

    def get(self, key: str) -> Tier | None:
        return next((t for t in self.tiers if t.key == key), None)

    @property
    def selectable(self) -> tuple[Tier, ...]:
        """Tiers a member may choose for themselves."""
        return tuple(t for t in self.tiers if not t.invite_only)


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    raw = json.loads(CATALOGUE_PATH.read_text())
    tiers = tuple(
        Tier(
            key=t["key"],
            name=t["name"],
            label=t["label"],
            bottles=int(t["bottles"]),
            discount_pct=int(t["discount_pct"]),
            price_cents=int(t["price_cents"]),
            shipping_cents=int(t["shipping_cents"]),
            invite_only=bool(t["invite_only"]),
            featured=bool(t["featured"]),
            summary=t["summary"],
            benefits=tuple(t["benefits"]),
        )
        for t in raw["tiers"]
    )
    keys = [t.key for t in tiers]
    if len(keys) != len(set(keys)):
        raise ValueError("tiers.json has duplicate keys")
    return Catalogue(
        currency=raw["currency"],
        shipments_per_year=int(raw["shipments_per_year"]),
        shipment_months=tuple(int(m) for m in raw["shipment_months"]),
        tiers=tiers,
    )
