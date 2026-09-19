"""
Fuzzy-matches customer names against SDN entity and alias names.
See DRD Data Processing and Test Plan for the design this
implements: exact match misses the entire point of screening (OFAC
publishes aliases precisely because sanctioned parties show up
under name variants), so this uses rapidfuzz with a score threshold
instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

DEFAULT_THRESHOLD = 88.0
# Set from real evidence, not a guess: testing the actual seeded test
# set (see seed_customers.sql) against the full ~44,000-candidate live
# pool found a genuine false positive at 85.71 ("Donna Thomas" against
# the real alias "DON TOMAS", a coincidental character-level match
# with zero relation to the customer), while the weakest real
# misspelled true positive scored 92.31. 88.0 sits in the gap with
# real margin on both sides. This will not be perfect against every
# possible name, short/generic aliases like "DON TOMAS" will keep
# producing occasional coincidental collisions no matter where the
# threshold sits, which is exactly why matches land in pending_review
# for a human to glance at, not as an automatic determination.

# Per DRD Data Processing: normalize case, punctuation, and corporate
# suffixes before matching, so formatting noise (a trailing "S.A." or
# a comma) doesn't distort scores that should be about the name itself.
CORPORATE_SUFFIXES = {
    "SA", "LTD", "LLC", "INC", "CORP", "CO", "GMBH", "AG",
    "LIMITED", "INCORPORATED", "CORPORATION", "COMPANY",
    "OOO",  # Russian: Obshchestvo s Ogranichennoy Otvetstvennostyu (LLC).
    # Confirmed present in real data; likely not the only non-English
    # suffix that exists, added as found rather than claimed complete.
}


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    text = name.upper()
    text = re.sub(r"[.,]", "", text)
    tokens = [t for t in text.split() if t not in CORPORATE_SUFFIXES]
    return " ".join(tokens)


@dataclass
class MatchCandidate:
    ent_num: int
    name: str
    matched_on: str  # 'primary_name' or 'alias', matches the
    # screening_matches.matched_on CHECK constraint in schema.sql


@dataclass
class Customer:
    id: int
    full_name: str


@dataclass
class Match:
    customer_id: int
    ent_num: int
    matched_name: str
    matched_on: str
    match_score: float


def build_candidates(
    entities: list[tuple[int, str]], aliases: list[tuple[int, str]]
) -> list[MatchCandidate]:
    """entities: (ent_num, primary_name) pairs. aliases: (ent_num,
    alias_name) pairs. Flattens both into one candidate list, each
    tagged with which kind of name it came from."""
    candidates = [MatchCandidate(ent_num=n, name=name, matched_on="primary_name") for n, name in entities]
    candidates += [MatchCandidate(ent_num=n, name=name, matched_on="alias") for n, name in aliases]
    return candidates


def find_matches(
    customers: list[Customer],
    candidates: list[MatchCandidate],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[Match]:
    """Every candidate scoring at or above threshold is returned, not
    just each customer's single best match: a customer can
    legitimately match more than one alias of the same entity (or,
    rarely, more than one entity), and screening_matches is designed
    to hold every such match, not collapse to one row per customer."""
    if not customers or not candidates:
        return []

    customer_names = [normalize_name(c.full_name) for c in customers]
    candidate_names = [normalize_name(c.name) for c in candidates]

    # token_sort_ratio, not WRatio: WRatio's partial-ratio component
    # scores short alias codenames (real ones exist in this data,
    # e.g. "MIT", "AQUA") as near-matches against almost any longer
    # name containing a similar substring ("James Smith" scored 90
    # against "MIT" purely because "MIT" sits inside "SMITH"),
    # discovered by testing against the full ~19,400-entity real
    # dataset, not visible at small scale. token_sort_ratio avoids
    # that while still correctly handling real name-order variation
    # (OFAC's own primary_name format is "LAST, First"; a customer's
    # name might come in as "First Last", and token_sort_ratio scores
    # that reordering as a perfect match, where plain ratio would
    # wrongly score it around 50).
    score_matrix = process.cdist(customer_names, candidate_names, scorer=fuzz.token_sort_ratio)

    matches: list[Match] = []
    for i, customer in enumerate(customers):
        for j, candidate in enumerate(candidates):
            score = score_matrix[i][j]
            if score >= threshold:
                matches.append(
                    Match(
                        customer_id=customer.id,
                        ent_num=candidate.ent_num,
                        matched_name=candidate.name,
                        matched_on=candidate.matched_on,
                        match_score=round(float(score), 2),
                    )
                )
    return matches
