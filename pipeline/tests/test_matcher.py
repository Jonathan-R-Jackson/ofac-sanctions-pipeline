"""
Tests matcher.py against real SDN entities (see
tests/fixtures/matcher_sample_entities.xml, 15 real entities
curated from a live pull, not invented names), following the Test
Plan's true-positive/true-negative/near-miss design.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "load_match_lambda"))

from matcher import Customer, build_candidates, find_matches, normalize_name  # noqa: E402
from parser import parse_entities_xml  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "matcher_sample_entities.xml"


@pytest.fixture
def candidates():
    entities = parse_entities_xml(FIXTURE.read_bytes())
    entity_pairs = [(e.ent_num, e.primary_name) for e in entities]
    alias_pairs = [(e.ent_num, a.alias_name) for e in entities for a in e.aliases]
    return build_candidates(entity_pairs, alias_pairs)


def test_exact_primary_name_matches(candidates):
    customers = [Customer(id=1, full_name="SAFAROV, Azamat")]
    matches = find_matches(customers, candidates)
    assert any(m.ent_num == 26686 and m.matched_on == "primary_name" for m in matches)


def test_exact_alias_matches(candidates):
    # STREL, Andrey is a real alias of ent_num 26668 (PLOTNITSKIY, Andrey)
    customers = [Customer(id=1, full_name="STREL, Andrey")]
    matches = find_matches(customers, candidates)
    assert any(m.ent_num == 26668 and m.matched_on == "alias" for m in matches)


def test_lightly_misspelled_name_still_matches(candidates):
    # One character removed from the real primary name
    customers = [Customer(id=1, full_name="SAFAROV, Azmat")]
    matches = find_matches(customers, candidates)
    assert any(m.ent_num == 26686 for m in matches)


def test_clearly_unrelated_name_does_not_match(candidates):
    customers = [Customer(id=1, full_name="Jennifer Anne Wilson")]
    matches = find_matches(customers, candidates)
    assert matches == []


def test_customer_can_match_more_than_one_alias_of_same_entity(candidates):
    # PLOTNITSKIY, Andrey (26668) has two real aliases: STREL, Andrey
    # and KOVALSKIY, Andrey Vechislavovich. A customer whose name is
    # close to both should produce two rows, not collapse to one,
    # matching how screening_matches is designed (see Data Dictionary).
    customers = [Customer(id=1, full_name="Andrey")]
    matches = find_matches(customers, candidates, threshold=50.0)  # loose threshold to surface both
    ent_26668_matches = [m for m in matches if m.ent_num == 26668]
    assert len(ent_26668_matches) >= 2


def test_corporate_suffix_normalization_handles_russian_ooo():
    # OOO is the Russian LLC-equivalent suffix, confirmed present in
    # real data (OPTIMA, OOO). Must not be treated as a name token
    # that drags the score down for an otherwise-identical company.
    assert normalize_name("OPTIMA, OOO") == "OPTIMA"
    assert normalize_name("Optima LLC") == "OPTIMA"


def test_near_miss_surname_reuse_observed_not_gated(candidates):
    # Real surname (SAFAROV) paired with an unrelated first name. Per
    # the Test Plan, this is observed to inform threshold calibration,
    # not asserted pass/fail against a number chosen in advance.
    customers = [Customer(id=1, full_name="SAFAROV, John")]
    matches = find_matches(customers, candidates, threshold=0.0)  # see every score
    same_surname = [m for m in matches if m.ent_num == 26686]
    assert len(same_surname) == 1
    print(f"\nNear-miss score for a shared surname, different first name: {same_surname[0].match_score}")


def test_match_score_and_matched_on_fit_schema_constraints(candidates):
    # Guards against ever violating screening_matches's CHECK
    # constraints (schema.sql) before a row even reaches the database.
    customers = [Customer(id=1, full_name="SAFAROV, Azamat")]
    matches = find_matches(customers, candidates)
    for m in matches:
        assert 0 <= m.match_score <= 100
        assert m.matched_on in ("primary_name", "alias")


def test_short_generic_alias_false_positive_stays_below_default_threshold(candidates):
    # Regression test for a real finding from seeding actual test
    # customers: "Donna Thomas" (a true negative with no relation to
    # any real SDN entity) scored 85.71 against the real alias "DON
    # TOMAS" (ent_num 9564), just above the threshold at the time
    # (85.0). This is why DEFAULT_THRESHOLD moved to 88.0, and this
    # test is here so a future change to normalization or scoring
    # can't silently let this specific false positive back in.
    customers = [Customer(id=1, full_name="Donna Thomas")]
    matches = find_matches(customers, candidates)  # real default threshold
    assert matches == []
