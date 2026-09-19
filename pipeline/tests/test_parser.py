import datetime
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extract_lambda"))
from parser import parse_entities_xml, parse_entity, count_entities, _strip_namespaces  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_entity.xml"


def load_fixture_entities():
    xml_bytes = FIXTURE.read_bytes()
    return parse_entities_xml(xml_bytes)


def test_parses_the_real_example_entity_without_error():
    entities = load_fixture_entities()
    assert len(entities) == 1


def test_core_fields():
    e = load_fixture_entities()[0]
    assert e.ent_num == 30629
    assert e.identity_id == 22313
    assert e.entity_type == "Entity"
    assert e.primary_name == "KAVE COFFEE S.A."


def test_multi_valued_programs_captured_as_a_list():
    e = load_fixture_entities()[0]
    assert e.sanctions_programs == ["CUBA"]


def test_alias_extracted_with_type():
    e = load_fixture_entities()[0]
    assert len(e.aliases) == 1
    assert e.aliases[0].alias_name == "AL-SAMAHY, Allaa"
    assert e.aliases[0].alias_type == "A.K.A."
    assert e.aliases[0].is_low_quality is False


def test_address_country_only():
    e = load_fixture_entities()[0]
    assert len(e.addresses) == 1
    assert e.addresses[0].country == "Turkey"


def test_address_parts_extracted():
    e = load_fixture_entities()[0]
    addr = e.addresses[0]
    assert addr.city == "Istanbul"
    assert addr.address1 == "123 Example Street"
    # Not present in the fixture; must come back None, not KeyError or ''
    assert addr.address2 is None
    assert addr.postal_code is None
    assert addr.region is None


def test_takes_the_primary_birthdate_when_two_are_present():
    # The fixture has two Birthdate features: an exact one (isPrimary=true,
    # 1976-09-08) and an approximate one (isPrimary=false, 1981). The
    # primary one must win.
    e = load_fixture_entities()[0]
    assert e.date_of_birth == datetime.date(1976, 9, 8)


def test_gender_captured():
    e = load_fixture_entities()[0]
    assert e.gender == "Male"


def test_count_entities_matches_full_parse_without_building_objects():
    assert count_entities(FIXTURE.read_bytes()) == len(load_fixture_entities())


def test_remarks_is_none_when_absent_from_source():
    # Per Data Dictionary Open Items: not confirmed to exist on the REST
    # response. The fixture has none, and the parser must not error.
    e = load_fixture_entities()[0]
    assert e.remarks is None


def test_rejects_an_unrecognized_entity_type():
    bad_xml = FIXTURE.read_text().replace(
        '<entityType refId="601">Entity</entityType>',
        '<entityType refId="999">Spaceship</entityType>',
    )
    root = _strip_namespaces(ET.fromstring(bad_xml))
    entity_el = root.find(".//entity")
    try:
        parse_entity(entity_el)
        assert False, "expected ValueError for an unrecognized entity type"
    except ValueError as exc:
        assert "Spaceship" in str(exc)


if __name__ == "__main__":
    import inspect
    tests = [f for name, f in list(globals().items()) if name.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL: {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
