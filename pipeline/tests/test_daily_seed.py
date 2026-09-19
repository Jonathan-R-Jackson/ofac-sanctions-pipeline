import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "load_match_lambda"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extract_lambda"))

from daily_seed import generate_daily_customers  # noqa: E402
from parser import parse_entities_xml  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_entity.xml"


def _fixture_entities():
    return parse_entities_xml(FIXTURE.read_bytes())


def test_generates_at_least_one_true_positive_and_some_noise():
    result = generate_daily_customers(_fixture_entities(), rng=random.Random(1))
    positives = [r for r in result if r[1] is True]
    negatives = [r for r in result if r[1] is False]
    assert len(positives) == 1
    assert 2 <= len(negatives) <= 3


def test_true_positive_name_is_real_or_a_light_misspelling_of_one():
    entities = _fixture_entities()
    real_names = {e.primary_name for e in entities}
    result = generate_daily_customers(entities, rng=random.Random(2))
    tp_name = [r[0] for r in result if r[1] is True][0]
    if tp_name not in real_names:
        # Must be a one-character edit of a real name, not something
        # unrelated: same length, differs in exactly one position.
        closest = min(real_names, key=lambda n: sum(a != b for a, b in zip(n, tp_name)) if len(n) == len(tp_name) else 999)
        assert len(closest) == len(tp_name)
        diff_count = sum(a != b for a, b in zip(closest, tp_name))
        assert diff_count == 1


def test_no_entities_still_produces_noise_customers():
    # Defensive case: an empty entity list (shouldn't happen in
    # practice, Extract already validates this, but the function
    # must not crash if it ever does) still returns the Faker noise.
    result = generate_daily_customers([], rng=random.Random(3))
    assert all(r[1] is False for r in result)
    assert len(result) >= 2


def test_is_deterministic_given_the_same_seeds():
    from faker import Faker

    entities = _fixture_entities()
    Faker.seed(42)
    result_a = generate_daily_customers(entities, rng=random.Random(42), fake=Faker())
    Faker.seed(42)
    result_b = generate_daily_customers(entities, rng=random.Random(42), fake=Faker())
    assert result_a == result_b
