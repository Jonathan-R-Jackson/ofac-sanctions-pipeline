"""
Generates a handful of new customers to add on each pipeline run,
so the customer population keeps growing with fresh cases day over
day instead of staying frozen at whatever was seeded once during
initial testing (see seed_customers.sql).

Deliberately additive, never deletes or regenerates existing
customers: screening_matches has ON DELETE CASCADE back to
customers specifically so a customer's match history disappears if
the customer row does, and that's exactly the audit-trail property
this project has been careful to preserve everywhere else. Adding
new rows sidesteps that entirely.

The one true-positive case is drawn from the entities just parsed
in this same run, not a static fixture, so it reflects whatever the
SDN list actually looks like today, not a snapshot from whenever
this was first tested.
"""
from __future__ import annotations

import random

from faker import Faker

from parser import ParsedEntity


def _misspell(name: str, rng: random.Random) -> str:
    """One-character edit, same technique used to build the original
    seed_customers.sql true-positive-misspelled cases."""
    letter_positions = [i for i, ch in enumerate(name) if ch.isalpha()]
    if not letter_positions:
        return name
    idx = rng.choice(letter_positions)
    chars = list(name)
    replacement = rng.choice("abcdefghijklmnopqrstuvwxyz")
    chars[idx] = replacement.upper() if chars[idx].isupper() else replacement
    return "".join(chars)


def generate_daily_customers(
    entities: list[ParsedEntity],
    rng: random.Random | None = None,
    fake: Faker | None = None,
) -> list[tuple[str, bool]]:
    """Returns (full_name, is_seeded_positive) pairs for today's new
    customers: one name drawn from today's actual loaded entities
    (exact half the time, lightly misspelled the other half, mirroring
    the original seed set's mix), plus 2-3 pure Faker names as noise.
    rng and fake are injectable for deterministic tests; production
    calls leave them unset for genuine day-to-day variation."""
    rng = rng or random.Random()
    fake = fake or Faker()

    new_customers: list[tuple[str, bool]] = []

    named_entities = [e for e in entities if e.primary_name]
    if named_entities:
        pick = rng.choice(named_entities)
        name = pick.primary_name
        if rng.random() < 0.5:
            name = _misspell(name, rng)
        new_customers.append((name, True))

    for _ in range(rng.randint(2, 3)):
        new_customers.append((fake.name(), False))

    return new_customers
